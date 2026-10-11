"""Tests of the job providers: parsers on fixtures, allowlist, HTTP layer, models."""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from urllib.parse import quote, quote_plus

import pytest

from cvmax.jobs import http
from cvmax.jobs import providers as P
from cvmax.jobs.models import JobQuery, Vacancy
from cvmax.jobs.providers import base
from cvmax.jobs.providers.base import (
    EU, REMOTE, UA, UK, US, allowed_url, clean_html, parse_date, parse_rss, requests_for,
)

FIX = Path(__file__).parent / "fixtures" / "jobs"


def prov(name: str):
    return next(p for p in P.ALL if p.name == name)


def q(region: str = EU, level: str = "Junior / first job", **kw) -> JobQuery:
    kw.setdefault("keywords", ("python",))
    return JobQuery(region=region, level=level, **kw)


def load(name: str) -> bytes:
    return (FIX / name).read_bytes()


# ---------- parsers ----------

def test_dou_parse():
    out = prov("DOU").parse(load("dou.xml"), "", q(UA))
    assert len(out) == 3
    a, b, c = out
    assert (a.title, a.company, a.location) == ("Junior Python Developer", "Nordlane", "Київ")
    assert a.url == "https://jobs.dou.ua/companies/nordlane/vacancies/100001/?utm_source=jobsrss"
    assert a.posted_at == "2026-04-16" and a.salary == "$800–1200" and a.remote is True
    assert "<" not in a.snippet and "&amp;" not in a.snippet and "fast learner" in a.snippet
    assert b.remote is False and b.salary is None and b.posted_at == "2026-04-15"
    assert "Postman" in b.snippet and "<b>" not in b.snippet
    assert c.posted_at is None and c.snippet == ""
    assert all(v.source == "DOU" for v in out)


def test_djinni_parse():
    out = prov("Djinni").parse(load("djinni.xml"), "", q(UA))
    assert len(out) == 3
    a, b, c = out
    assert a.location == "Remote" and a.remote is True
    assert a.salary == "$1,000–$1,500 USD per month" and a.posted_at == "2026-09-16"
    assert b.company == "Lumenfield" and b.location == "Kyiv"
    assert c.company == "" and c.posted_at is None
    assert not any("evil" in v.url for v in out)


def test_jooble_parse_only_own_https_links():
    out = prov("Jooble").parse(load("jooble.json"), "", q(UA))
    assert [v.company for v in out] == ["Orchard Labs", ""]
    assert out[0].title == "Junior Python Developer"
    assert out[0].snippet == "Work with Django & REST APIs..."
    assert out[0].posted_at == "2026-10-07"
    assert all(v.url.startswith("https://") and "jooble.org" in v.url for v in out)


def test_adzuna_parse():
    out = prov("Adzuna").parse(
        load("adzuna.json"), "https://api.adzuna.com/v1/api/jobs/gb/search/1?x", q(UK))
    assert len(out) == 3
    a, b, c = out
    assert a.company == "Harbor & Pine" and a.salary == "£28,000–32,000"
    assert a.posted_at == "2026-10-01" and a.snippet.startswith("Analyse data")
    assert b.salary is None  # the predicted salary is not shown
    assert c.url.endswith("/333")
    assert not any("evil" in v.url or "notadzuna" in v.url for v in out)


def test_arbeitnow_parse_filters_locally():
    out = prov("Arbeitnow").parse(load("arbeitnow.json"), "", q(EU))
    assert [v.company for v in out] == ["Rheinwerk GmbH", "Nordlicht AG", "Elbtal UG"]
    a = out[0]
    assert a.location.startswith("Köln") and a.posted_at == "2026-10-08"
    assert a.remote is False and "Python" in a.tags
    assert out[2].posted_at is None
    only_remote = prov("Arbeitnow").parse(load("arbeitnow.json"), "", q(REMOTE))
    assert [v.company for v in only_remote] == ["Nordlicht AG"]


def test_remotive_parse():
    out = prov("Remotive").parse(load("remotive.json"), "", q(REMOTE))
    assert len(out) == 3
    a = out[0]
    assert a.company == "Pebblebrook" and a.salary == "$40,000 - $50,000"
    assert a.posted_at == "2026-10-06" and a.remote is True and a.location == "Worldwide"
    assert out[2].posted_at is None
    assert all(v.url.startswith("https://remotive.com/") for v in out)


def test_jobicy_parse():
    out = prov("Jobicy").parse(load("jobicy.json"), "", q(REMOTE))
    assert len(out) == 3
    a, b, c = out
    assert a.salary == "EUR 30,000–40,000 yearly" and a.posted_at == "2026-10-08"
    assert a.snippet == "Help users – fast & kindly…"
    assert b.tags == ("Marketing", "Internship") and b.snippet == "Social media tasks."
    assert c.company == ""
    assert not any("evil" in v.url for v in out)


def test_greenhouse_parse():
    out = prov("Greenhouse").parse(load("greenhouse.json"), "", q(EU, company="Larkspur"))
    assert [v.title for v in out] == [
        "Junior Python Engineer", "Software Engineer Intern", "Python Analyst"]
    a = out[0]
    assert a.company == "Larkspur" and a.remote is True and a.posted_at == "2026-09-09"
    assert a.snippet == "Build Python services & tools."  # doubly escaped HTML
    assert all("greenhouse.io" in v.url for v in out)


def test_lever_parse():
    out = prov("Lever").parse(load("lever.json"), "", q(EU, company="Tidepool"))
    assert len(out) == 3
    a = out[0]
    assert a.title == "Junior Python Developer" and a.remote is True
    assert a.salary == "USD 60,000–80,000" and a.posted_at == "2026-08-31"
    assert a.url == "https://jobs.lever.co/tidepool/a1"
    assert not any("evil" in v.url for v in out)


PARSED = ["DOU", "Djinni", "Jooble", "Adzuna", "Arbeitnow", "Remotive", "Jobicy",
          "Greenhouse", "Lever"]


@pytest.mark.parametrize("name", PARSED)
def test_malformed_and_empty_input_returns_empty(name):
    p = prov(name)
    query = q(EU, company="Larkspur")
    for body in (load("malformed.xml"), load("malformed.json"), b"", b"null", b"[]", b"{}",
                 b"\xff\xfe\x00bad"):
        assert p.parse(body, "", query) == []


@pytest.mark.parametrize("name", PARSED)
def test_parsed_vacancies_are_safe(name):
    fixture = {"DOU": "dou.xml", "Djinni": "djinni.xml"}.get(name, f"{name.lower()}.json")
    out = prov(name).parse(load(fixture), "", q(EU, company="Larkspur"))
    assert out
    for v in out:
        assert isinstance(v, Vacancy) and v.source == name
        assert re.fullmatch(r"https?://[^\s\\]+", v.url)
        assert len(v.snippet) <= 400 and "<" not in v.snippet
        assert len(v.id) == 12


# ---------- full descriptions ----------

def fixture_file(name: str) -> str:
    return {"DOU": "dou.xml", "Djinni": "djinni.xml"}.get(name, f"{name.lower()}.json")


def _rss(link: str, description: str, title: str) -> bytes:
    return (f'<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><item>'
            f"<title>{title}</title><link>{link}</link>"
            f"<description><![CDATA[{description}]]></description></item></channel></rss>"
            ).encode()


def _json(obj: object) -> bytes:
    return json.dumps(obj).encode()


# One posting per provider in the provider's own raw format: name -> (body from a description, query).
RAW_POSTING = {
    "DOU": (lambda d: _rss("https://jobs.dou.ua/companies/a/vacancies/1/", d,
                           "Python Developer в Acme, Kyiv"), q(UA)),
    "Djinni": (lambda d: _rss("https://djinni.co/jobs/1-python/", d, "Python Developer"), q(UA)),
    "Arbeitnow": (lambda d: _json({"data": [{
        "title": "Python Developer", "company_name": "Acme", "description": d, "remote": False,
        "url": "https://www.arbeitnow.com/jobs/companies/acme/py", "location": "Berlin"}]}), q(EU)),
    "Remotive": (lambda d: _json({"jobs": [{
        "url": "https://remotive.com/remote-jobs/dev/py-1", "title": "Python Developer",
        "company_name": "Acme", "description": d}]}), q(REMOTE)),
    "Jobicy": (lambda d: _json({"jobs": [{
        "url": "https://jobicy.com/jobs/1-py", "jobTitle": "Python Developer",
        "jobDescription": d}]}), q(REMOTE)),
    "Greenhouse": (lambda d: _json({"jobs": [{
        "title": "Python Developer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
        "location": {"name": "Berlin"}, "content": d}]}), q(EU, company="Acme")),
    "Lever": (lambda d: _json([{
        "text": "Python Developer", "hostedUrl": "https://jobs.lever.co/acme/1", "categories": {},
        "descriptionPlain": d}]), q(EU, company="Acme")),
    "Jooble": (lambda d: _json({"jobs": [{
        "title": "Python Developer", "link": "https://jooble.org/desc/1", "snippet": d,
        "company": "Acme"}]}), q(UA)),
    "Adzuna": (lambda d: _json({"results": [{
        "title": "Python Developer", "redirect_url": "https://www.adzuna.co.uk/jobs/land/ad/1",
        "description": d}]}), q(UK)),
}
assert set(RAW_POSTING) == set(PARSED)

# About 4000 characters: well over the 400-character snippet, well under the 6000 cap.
LONG_TEXT = " ".join(f"Requirement {i}: knows tool{i} well." for i in range(1, 121))

# The Jobicy fixture has a separate teaser (jobExcerpt) next to a different full text.
EXCERPT_IS_NOT_A_PREFIX = {("Jobicy", "Junior Support Engineer")}


@pytest.mark.parametrize("name", PARSED)
def test_fixture_descriptions_start_with_the_snippet(name):
    out = prov(name).parse(load(fixture_file(name)), "", q(EU, company="Larkspur"))
    assert out
    for v in out:
        assert isinstance(v.description, str) and len(v.description) <= base.DESCRIPTION_LEN
        assert "<" not in v.description and "&amp;" not in v.description
        if (name, v.title) in EXCERPT_IS_NOT_A_PREFIX:
            continue
        head = v.snippet.removesuffix("…")  # a cut snippet ends with an ellipsis
        # The snippet is one line; the description has lines and bullets. Same words, same order.
        flat, head = (re.sub(r"[\W_]+", "", t) for t in (v.description, head))
        assert flat.startswith(head) and len(flat) >= len(head)


def test_fixture_descriptions_exact_text():
    dou = prov("DOU").parse(load("dou.xml"), "", q(UA))
    assert dou[0].snippet == "We need a junior developer & fast learner. Python SQL"
    assert dou[0].description == "We need a junior developer & fast learner.\n- Python\n- SQL"
    assert dou[1].description == "Manual testing, Postman and Jira." and dou[2].description == ""
    djinni = prov("Djinni").parse(load("djinni.xml"), "", q(UA))
    assert djinni[0].description == djinni[0].snippet and "Build APIs & tools." in djinni[0].description
    green = prov("Greenhouse").parse(load("greenhouse.json"), "", q(EU, company="Larkspur"))
    assert green[0].snippet == green[0].description == "Build Python services & tools."  # doubly escaped
    assert prov("Greenhouse").parse(_json({"jobs": [{
        "title": "Python Developer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
        "content": "&lt;p&gt;Must have:&lt;/p&gt;&lt;ul&gt;&lt;li&gt;SQL &amp;amp; Python&lt;/li&gt;"
                   "&lt;li&gt;Git&lt;/li&gt;&lt;/ul&gt;&lt;p&gt;Nice:&lt;br&gt;Tableau&lt;/p&gt;"}]}),
        "", q(EU, company="Acme"))[0].description == "Must have:\n- SQL & Python\n- Git\nNice:\nTableau"
    assert green[2].description == ""
    remotive = prov("Remotive").parse(load("remotive.json"), "", q(REMOTE))
    assert remotive[0].snippet == "About Join us & build."
    assert remotive[0].description == "About\nJoin us & build." and remotive[2].description == ""
    lever = prov("Lever").parse(load("lever.json"), "", q(EU, company="Tidepool"))
    assert lever[0].description == "Write Python code & tests."


@pytest.mark.parametrize("name", PARSED)
def test_long_text_goes_to_description_while_snippet_stays_short(name):
    make, query = RAW_POSTING[name]
    (v,) = prov(name).parse(make(LONG_TEXT), "", query)
    assert len(LONG_TEXT) > 3000
    assert len(v.snippet) == 400 and v.snippet.endswith("…")
    assert v.description == LONG_TEXT
    assert v.description.startswith(v.snippet.removesuffix("…"))


@pytest.mark.parametrize("name", PARSED)
def test_description_of_20000_characters_is_cut_to_6000(name):
    make, query = RAW_POSTING[name]
    (v,) = prov(name).parse(make("x" * 20000), "", query)
    assert base.DESCRIPTION_LEN == 6000
    assert len(v.description) == 6000 and v.description.endswith("…")
    assert len(v.snippet) == 400


@pytest.mark.parametrize("name", PARSED)
def test_to_row_has_no_description(name):
    out = prov(name).parse(load(fixture_file(name)), "", q(EU, company="Larkspur"))
    assert out and any(v.description for v in out)
    for v in out:
        assert "description" not in v.to_row()


def test_description_is_plain_text_for_hostile_html():
    markup = "<script>evil()</script><h3>About</h3>" + "<li>Skill &amp; tool</li>" * 2000
    v = base.make_vacancy(url="https://remotive.com/a", title="T", source="Remotive",
                          domains=("remotive.com",), description=markup)
    assert v.description.startswith("About\n- Skill & tool\n- Skill & tool")
    assert "<" not in v.description and "evil" not in v.description
    assert 5980 <= len(v.description) <= base.DESCRIPTION_LEN and v.description.endswith("\n…")
    assert v.description.splitlines()[-2] == "- Skill & tool"  # cut at a line boundary


def test_make_vacancy_description():
    kw = dict(url="https://jobs.dou.ua/a", title="T", source="DOU", domains=("dou.ua",))
    assert base.make_vacancy(**kw).description == ""
    v = base.make_vacancy(**kw, snippet="<p>Short</p>", description="<p>Short</p><p>and long &amp; more</p>")
    assert v.snippet == "Short" and v.description == "Short\nand long & more"
    for bad in (None, 5, ["x"], {"a": 1}):
        assert base.make_vacancy(**kw, description=bad).description == ""
    assert len(base.make_vacancy(**kw, description="y" * 7000).description) == base.DESCRIPTION_LEN


def test_lever_description_includes_list_sections():
    body = _json([{
        "text": "Python Developer", "hostedUrl": "https://jobs.lever.co/acme/1", "categories": {},
        "descriptionPlain": "Build services.",
        "lists": [{"text": "What you will need:", "content": "<li>3 years of Python</li><li>SQL &amp; Postgres</li>"},
                  "junk", {"text": ["x"], "content": None}],
        "additionalPlain": "We value kindness."}])
    (v,) = prov("Lever").parse(body, "", q(EU, company="Acme"))
    assert v.snippet == "Build services."
    assert v.description == ("Build services.\n\nWhat you will need:\n- 3 years of Python\n- SQL & Postgres"
                             "\n\nWe value kindness.")
    html_only = _json([{"text": "Python Developer", "hostedUrl": "https://jobs.lever.co/acme/2",
                        "categories": {}, "description": "<p>Hello <b>there</b></p>"}])
    (w,) = prov("Lever").parse(html_only, "", q(EU, company="Acme"))
    assert w.snippet == "" and w.description == "Hello there"


def test_jobicy_description_prefers_the_full_text_over_the_excerpt():
    a, b, c = prov("Jobicy").parse(load("jobicy.json"), "", q(REMOTE))
    assert a.snippet == "Help users – fast & kindly…" and a.description == "Long"
    assert b.description == b.snippet == "Social media tasks."
    assert c.description == c.snippet == ""
    teaser = _json({"jobs": [{"url": "https://jobicy.com/jobs/9-x", "jobTitle": "X",
                              "jobExcerpt": "Only a teaser."}]})
    (v,) = prov("Jobicy").parse(teaser, "", q(REMOTE))
    assert v.snippet == v.description == "Only a teaser."


def test_description_never_reaches_the_database_rows():
    from cvmax.db import MemoryDB
    out = prov("Remotive").parse(load("remotive.json"), "", q(REMOTE))
    assert out[0].description
    rows = [{**v.to_row(), "region": REMOTE, "role_family": "python", "query": "python"} for v in out]
    db = MemoryDB()
    assert db.upsert_vacancies(rows) == len(out)
    assert all("description" not in r for r in db.vacancies.values())


# ---------- build_urls, keys, search_url ----------

def test_build_urls_encode_keywords():
    query = q(UA, keywords=("data analyst", "c++ & sql"))
    urls = prov("DOU").build_urls(query, {})
    assert urls[0].startswith("https://jobs.dou.ua/vacancies/feeds/?search=data+analyst")
    assert "exp=0-1" in urls[0] and "search=c%2B%2B+%26+sql" in urls[1]
    assert "remote" not in urls[0]
    assert prov("DOU").build_urls(q(UK), {}) == []
    dj = prov("Djinni").build_urls(query, {})
    assert dj[0] == "https://djinni.co/jobs/rss/?primary_keyword=data+analyst"
    an = prov("Arbeitnow").build_urls(q(EU, keywords=("a&b=c",)), {})
    assert an == ["https://www.arbeitnow.com/api/job-board-api?search=a%26b%3Dc"]


def test_remote_flag_in_dou():
    assert prov("DOU").build_urls(q(REMOTE), {})[0].endswith("&remote&exp=0-1")
    assert "remote" in prov("DOU").build_urls(q(UA, remote_ok=True), {})[0]


@pytest.mark.parametrize("name,env", [
    ("Jooble", {"JOOBLE_API_KEY": "abcd1234abcd"}),
    ("Adzuna", {"ADZUNA_APP_ID": "abc123", "ADZUNA_APP_KEY": "k3y456"}),
])
def test_keyed_providers_need_env(name, env):
    p = prov(name)
    query = q(EU)
    assert requests_for(p, query, {}) == []
    assert not P.base.has_needs(p, {})
    assert p not in P.fetchers(query, {})
    assert p in P.fetchers(query, env)
    some_missing = dict(list(env.items())[:-1]) if len(env) > 1 else {}
    assert p not in P.fetchers(query, some_missing)
    assert requests_for(p, query, env)


def test_jooble_requests_and_key_validation():
    p = prov("Jooble")
    reqs = requests_for(p, q(EU), {"JOOBLE_API_KEY": "abcd1234abcd"})
    assert [r[1]["location"] for r in reqs] == ["Germany", "Poland"]
    assert reqs[0][0] == "https://jooble.org/api/abcd1234abcd"
    assert reqs[0][1]["keywords"] == "python"
    assert [r[1]["location"] for r in requests_for(p, q(UA), {"JOOBLE_API_KEY": "abcd1234abcd"})] == ["Ukraine"]
    assert requests_for(p, q(UA), {"JOOBLE_API_KEY": "../../evil?x=1"}) == []


def test_adzuna_countries_and_quoting():
    env = {"ADZUNA_APP_ID": "abc123", "ADZUNA_APP_KEY": "k3y456"}
    p = prov("Adzuna")
    urls = p.build_urls(q(EU, keywords=("data analyst",)), env)
    assert [u.split("/jobs/")[1][:2] for u in urls] == ["de", "pl", "nl"]
    assert "what=data+analyst" in urls[0] and "app_id=abc123" in urls[0]
    assert [u.split("/jobs/")[1][:2] for u in p.build_urls(q(US), env)] == ["us", "ca"]
    assert p.build_urls(q(UA), env) == []
    assert p.build_urls(q(EU), {"ADZUNA_APP_ID": "a b", "ADZUNA_APP_KEY": "k3y456"}) == []


def test_remotive_only_for_remote_wishes():
    p = prov("Remotive")
    assert p.build_urls(q(UK), {}) == []
    assert p.build_urls(q(UK, remote_ok=True), {})
    assert p.build_urls(q(REMOTE), {})


def test_company_providers_need_valid_slug():
    for name, host in (("Greenhouse", "boards-api.greenhouse.io"), ("Lever", "api.lever.co")):
        p = prov(name)
        assert p.build_urls(q(EU), {}) == []
        assert p not in P.fetchers(q(EU), {})
        urls = p.build_urls(q(EU, company="Acme Corp"), {})
        assert len(urls) == 1 and host in urls[0] and "acmecorp" in urls[0]
        assert p in P.fetchers(q(EU, company="Acme Corp"), {})
        assert p.build_urls(q(EU, company="Acme/../x"), {}) == []
        assert p.build_urls(q(EU, company="Ünï"), {}) == []


def test_search_url_contains_encoded_keyword():
    for kw in ("python", "data analyst"):
        for region in (UA, UK, US, EU, REMOTE):
            query = q(region, keywords=(kw,))
            variants = {quote_plus(kw), quote(kw), kw.replace(" ", "-")}
            for p in P.ALL:
                url = p.search_url(query)
                if url is None:
                    continue
                assert any(v in url for v in variants), (p.name, url)
                assert allowed_url(url, p.domains), (p.name, url)


def test_deeplink_only_providers_have_search_url():
    for name in ("DOU", "Djinni", "Work.ua", "Robota.ua", "LinkedIn", "Indeed", "Glassdoor"):
        assert prov(name).search_url(q(UK if name in ("Indeed", "Glassdoor") else UA))
    for name in ("Work.ua", "Robota.ua", "LinkedIn", "Indeed", "Glassdoor"):
        p = prov(name)
        assert p.kind == "deeplink" and p.build_urls(q(UA), {}) == []
        assert p.parse(b"<html></html>", "", q(UA)) == []


def test_linkedin_url_flags():
    url = prov("LinkedIn").search_url(q(UA, level="Internship", keywords=("junior python",)))
    assert "keywords=junior+python" in url and "location=Ukraine" in url
    assert "f_E=1%2C2" in url and "f_TPR=r2592000" in url and "f_WT" not in url
    mid = prov("LinkedIn").search_url(q(REMOTE, level="Mid-level"))
    assert "f_E" not in mid and "f_WT=2" in mid and "Worldwide" in mid


def test_deep_links_order_and_fetchers():
    names = [n for n, _ in P.deep_links(q(UA))]
    assert names[0] == "LinkedIn"
    assert names.index("DOU") < names.index("Work.ua") < len(names)
    uk = [n for n, _ in P.deep_links(q(UK))]
    assert uk[0] == "LinkedIn" and uk[-2:] == ["Indeed", "Glassdoor"]
    assert [p.name for p in P.fetchers(q(UA), {})] == ["DOU", "Djinni"]
    assert all(p.kind != "deeplink" for p in P.fetchers(q(REMOTE), {}))
    assert {p.name for p in P.for_region(UA)} >= {"DOU", "Work.ua", "LinkedIn"}


# ---------- allowlist and helpers ----------

@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "data:text/html,hi", "ftp://jobs.dou.ua/x",
    "https://user:pw@jobs.dou.ua/x", "https://jobs.dou.ua@evil.com/x",
    "https://jobs.dou.ua.evil.com/x", "https://evildou.ua/x", "https://evil.com/jobs.dou.ua",
    "https://jobs.dou.ua:8443/x", "https://jobs.dou.ua/x y", "//jobs.dou.ua/x", "", None,
    "https:///x", "https://jobs.dou.ua\\@evil.com/",
])
def test_allowed_url_rejects(url):
    assert not allowed_url(url, ("jobs.dou.ua", "dou.ua"))


def test_allowed_url_accepts():
    d = ("jobs.dou.ua", "dou.ua")
    assert allowed_url("https://jobs.dou.ua/a?b=1", d)
    assert allowed_url("http://JOBS.DOU.UA/a", d)
    assert allowed_url("https://www.dou.ua/x", d)


def test_helpers():
    assert parse_date("Thu, 16 Apr 2026 15:25:00 +0300") == "2026-04-16"
    assert parse_date("2026-10-08T10:45:12+00:00") == "2026-10-08"
    assert parse_date("2013-11-08T18:07:39Z") == "2013-11-08"
    assert parse_date("2026-10-08") == "2026-10-08"
    assert parse_date(1791491475) == "2026-10-08"
    assert parse_date(1788199939703) == "2026-08-31"
    for bad in ("", None, "nope", "2026-13-40", True, {}):
        assert parse_date(bad) is None
    assert clean_html("<p>a&nbsp;b</p><script>x()</script>&lt;i&gt;c&lt;/i&gt;") == "a b c"
    assert len(clean_html("x" * 1000)) == 400
    assert base.quote_kw("  c++  dev ") == "c%2B%2B+dev"
    assert parse_rss(b"<not xml") == []
    xxe = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY e "boom">]><rss><channel><item><title>&e;</title></item></channel></rss>'
    assert parse_rss(xxe) == []
    atom = (b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>T</title>'
            b'<link href="https://x.example/a"/><summary>S</summary><updated>2026-01-02T00:00:00Z</updated>'
            b'<id>1</id></entry></feed>')
    row = parse_rss(atom)[0]
    assert (row["title"], row["link"], row["description"], row["guid"]) == ("T", "https://x.example/a", "S", "1")


def test_make_vacancy_allowlist():
    ok = base.make_vacancy(url="https://jobs.dou.ua/a", title="T", source="DOU", domains=("dou.ua",))
    assert ok and ok.company == "" and ok.tags == ()
    assert base.make_vacancy(url="https://evil.com/a", title="T", source="x", domains=("dou.ua",)) is None
    assert base.make_vacancy(url="https://jobs.dou.ua/a", title="  ", source="x", domains=("dou.ua",)) is None


# ---------- HTTP layer ----------

class FakeResp:
    def __init__(self, status=200, chunks=(b"ok",)):
        self.status_code = status
        self._chunks = chunks
        self.closed = False

    def iter_content(self, chunk_size=1):
        yield from self._chunks

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.calls = resp, exc, []

    def get(self, url, **kw):
        self.calls.append(("get", url, kw))
        if self.exc:
            raise self.exc
        return self.resp

    def post(self, url, **kw):
        self.calls.append(("post", url, kw))
        if self.exc:
            raise self.exc
        return self.resp


@pytest.fixture(autouse=True)
def _clean_cache():
    http.clear_cache()
    yield
    http.clear_cache()


def test_fetch_ok_and_request_options():
    s = FakeSession(FakeResp(200, (b"ab", b"cd")))
    assert http.fetch("https://x.example/a", session=s) == b"abcd"
    _, _, kw = s.calls[0]
    assert kw["allow_redirects"] is False and kw["stream"] is True and kw["timeout"][0] == 3.0 and 7.0 < kw["timeout"][1] <= 8.0
    assert kw["headers"]["User-Agent"] == http.USER_AGENT == "GetCVmax/1.0 (+https://getcvmax.com)"
    assert s.resp.closed


@pytest.mark.parametrize("status", [301, 302, 307, 403, 404, 500, 204])
def test_fetch_none_on_non_200(status):
    assert http.fetch("https://x.example/a", session=FakeSession(FakeResp(status))) is None


def test_fetch_none_when_too_big():
    s = FakeSession(FakeResp(200, (b"a" * 60, b"b" * 60)))
    assert http.fetch("https://x.example/a", max_bytes=100, session=s) is None
    assert s.resp.closed
    assert http.fetch("https://x.example/a", max_bytes=120, session=FakeSession(FakeResp(200, (b"a" * 60, b"b" * 60)))) == b"a" * 60 + b"b" * 60


def test_fetch_never_raises():
    import requests
    assert http.fetch("https://x.example/a", session=FakeSession(exc=requests.ConnectionError())) is None
    assert http.fetch("https://x.example/a", session=FakeSession(exc=RuntimeError("boom"))) is None
    assert http.fetch("file:///etc/passwd") is None
    assert http.fetch("javascript:1") is None


def test_fetch_slow_drip_hits_deadline():
    import time

    class Drip(FakeResp):
        def iter_content(self, chunk_size=1):
            assert chunk_size == 4096
            for _ in range(1000):
                time.sleep(0.05)
                yield b"x"

    t0 = time.monotonic()
    assert http.fetch("https://x.example/a", timeout=0.2, session=FakeSession(Drip())) is None
    assert time.monotonic() - t0 < 0.5


def test_fetch_post():
    s = FakeSession(FakeResp(200, (b'{"jobs": []}',)))
    assert http.fetch_post("https://x.example/a", {"k": 1}, session=s) == b'{"jobs": []}'
    assert s.calls[0][0] == "post" and s.calls[0][2]["json"] == {"k": 1}
    assert http.fetch_post("https://x.example/a", {}, session=FakeSession(FakeResp(302))) is None


def test_cache_and_fetch_many(monkeypatch):
    calls = []

    def fake_fetch(url, **kw):
        calls.append(url)
        return None if "bad" in url else url.encode()

    monkeypatch.setattr(http, "fetch", fake_fetch)
    assert http.cached_fetch("https://x.example/1") == b"https://x.example/1"
    assert http.cached_fetch("https://x.example/1") == b"https://x.example/1"
    assert calls == ["https://x.example/1"]
    http.clear_cache()
    assert http.cached_fetch("https://x.example/1")
    assert http.cached_fetch("https://x.example/bad") is None
    assert http.cached_fetch("https://x.example/bad") is None  # failures are not cached
    res = http.fetch_many(["https://x.example/a", "https://x.example/bad", "https://x.example/a"])
    assert res == {"https://x.example/a": b"https://x.example/a", "https://x.example/bad": None}
    boom = http.fetch_many(["https://x.example/a"], fetch=lambda u: 1 / 0)
    assert boom == {"https://x.example/a": None}


def test_cache_eviction(monkeypatch):
    monkeypatch.setattr(http, "fetch", lambda url, **kw: b"x")
    for i in range(http.CACHE_MAX + 20):
        http.cached_fetch(f"https://x.example/{i}")
    assert len(http._cache) == http.CACHE_MAX
    assert "https://x.example/0" not in http._cache


# ---------- models ----------

def test_vacancy_id_key_row():
    v = Vacancy(url="https://a.example/1", title="Junior  Python-Dev!", company="Acme, Inc.",
                location="Kyiv", source="DOU", posted_at="2026-01-01", salary=None,
                snippet="s", remote=None)
    assert re.fullmatch(r"[0-9a-f]{12}", v.id)
    assert v.key == "junior python dev|acme inc"
    row = v.to_row()
    assert set(row) == {"url", "source", "title", "company", "location", "posted_at",
                        "salary", "snippet", "remote"}
    with pytest.raises(Exception):
        v.title = "x"  # type: ignore[misc]


def test_vacancy_description_lives_in_memory_only():
    kw = dict(url="https://a.example/1", title="Junior Dev", company="Acme", location="Kyiv",
              source="DOU", posted_at=None, salary=None, snippet="s", remote=None)
    plain = Vacancy(**kw)
    full = Vacancy(**kw, description="Full posting text. " * 50)
    assert plain.description == ""
    assert "description" not in full.to_row() and set(full.to_row()) == set(plain.to_row())
    assert (full.id, full.key) == (plain.id, plain.key)
    assert full == plain and hash(full) == hash(plain)  # the long text takes no part in dedupe or caching
    assert "Full posting" not in repr(full)
    with pytest.raises(Exception):
        full.description = "x"  # type: ignore[misc]


def test_jobquery_validation():
    with pytest.raises(ValueError):
        JobQuery(keywords=tuple("abcdef"), region=EU, level="Mid-level")
    with pytest.raises(ValueError):
        JobQuery(keywords=(), region=EU, level="Mid-level")
    with pytest.raises(ValueError):
        JobQuery(keywords=("python",), region="Mars", level="Mid-level")
    with pytest.raises(ValueError):
        JobQuery(keywords=("python",), region=EU, level="Wizard")
    with pytest.raises(ValueError):
        JobQuery(keywords=("  \n\t ",), region=EU, level="Mid-level")
    with pytest.raises(ValueError):
        JobQuery(keywords=(5,), region=EU, level="Mid-level")  # type: ignore[arg-type]


def test_jobquery_cleaning():
    jq = JobQuery(keywords=("  py\x00thon\nlead ", "x" * 100, "Python Lead"), region=UA,
                  level="Internship", company="  Acme\x07 Corp " + "z" * 100)
    assert jq.keywords[0] == "py thon lead"
    assert all("\x00" not in k and "\n" not in k and len(k) <= 60 for k in jq.keywords)
    assert len(jq.company) <= 80 and "\x07" not in jq.company
    assert jq.primary == jq.keywords[0]
    assert jq.wants_entry_level
    assert not JobQuery(keywords=("a",), region=UA, level="Mid-level").wants_entry_level
    assert JobQuery(keywords=("a",), region=UA, level="Junior / first job").wants_entry_level


def test_clean_html_adversarial_input_is_fast():
    import time
    for body in ("<script " * 250000, "<" * 2_000_000, "<style a" * 100000):
        t0 = time.perf_counter()
        base.clean_html(body)
        base.clean_html(body, limit=None)
        assert time.perf_counter() - t0 < 1.0


def test_clean_html_still_strips_blocks_and_tags():
    out = base.clean_html("<p>Hi</p><script>var a=1<2;</script><style>p{}</style> &lt;b&gt;x&lt;/b&gt;")
    assert out == "Hi x"


# ---------- description keeps its line structure ----------

POSTING_HTML = "<p>Requirements:</p><ul><li>SQL</li><li>Python</li></ul><p>Nice:<br>Tableau</p>"
POSTING_TEXT = "Requirements:\n- SQL\n- Python\nNice:\nTableau"


def test_description_keeps_lines_and_bullets_but_clean_html_is_unchanged():
    kw = dict(url="https://remotive.com/a", title="T", source="Remotive", domains=("remotive.com",))
    v = base.make_vacancy(**kw, snippet=POSTING_HTML, description=POSTING_HTML)
    assert v.description == POSTING_TEXT
    assert v.snippet == "Requirements: SQL Python Nice: Tableau"  # the snippet stays one line
    assert clean_html(POSTING_HTML) == "Requirements: SQL Python Nice: Tableau"
    assert "\n" not in clean_html("<p>a</p><br><li>b</li>", None)
    assert v.title == "T" and "\n" not in base.make_vacancy(**{**kw, "title": "<p>A</p><p>B</p>"}).title


@pytest.mark.parametrize("markup, expected", [
    ("<h1>Title</h1>Text<div>Block</div><table><tr><td>a</td><td>b</td></tr><tr><td>c</td></tr></table>",
     "Title\nText\nBlock\na b\nc"),
    ("<P>Up</P><BR/><LI CLASS=x>One</LI><li>Two", "Up\n\n- One\n- Two"),  # case, <br/>, attributes
    ("Intro:<ul><li>A</li><li><b>B</b> and <i>C</i></li></ul>Outro", "Intro:\n- A\n- B and C\nOutro"),
    ("<div><div><p>Deep</p></div></div><div>Next</div>", "Deep\nNext"),  # nested blocks: one break
    ("<ul><li></li><li>Real</li></ul>", "- Real"),  # an empty bullet is dropped
    ("One<br><br><br><br>Two", "One\n\nTwo"),  # many blank lines collapse to one
    ("  <p>  spaced \t\t out&nbsp;&nbsp;words  </p>\n\n  <p>x</p> ", "spaced out words\nx"),
    ("<p>A &amp; B &lt; C &#8211; D</p><script>evil()</script><style>p{}</style>", "A & B < C – D"),
    ("<p>ctrl\x00\x01\x07chars\x7f&#1;</p>", "ctrlchars"),
    ("a<p>b</p>\r\nc d\r\ne", "a\nb\nc d e"),  # source newlines in HTML are whitespace
    ("&lt;p&gt;Escaped&lt;/p&gt;&lt;ul&gt;&lt;li&gt;X&lt;/li&gt;&lt;/ul&gt;", "Escaped\n- X"),
])
def test_clean_description_cases(markup, expected):
    assert base.clean_description(markup, None) == expected


def test_clean_description_plain_text_keeps_its_paragraphs():
    text = "Intro line.\r\n\r\n\r\n\r\nSecond   paragraph\twith  gaps.\nthird line  "
    assert base.clean_description(text) == "Intro line.\n\nSecond paragraph with gaps.\nthird line"
    # Tag-free input is plain text by itself; `plain=True` forces it when tags are present too.
    assert base.clean_description("a\nb <b>c</b>\nd", plain=True) == "a\nb c\nd"
    assert base.clean_description("a\nb <b>c</b>\nd") == "a b c d"
    assert base.clean_description(POSTING_TEXT) == POSTING_TEXT
    for bad in (None, 5, ["x"], {"a": 1}, "", "  \n\n  ", "<p></p><br>"):
        assert base.clean_description(bad) == ""


def test_clean_description_cuts_at_a_line_boundary():
    lines = [f"- Requirement number {i:04d}" for i in range(1000)]
    out = base.clean_description("\n".join(lines))
    assert len(out) <= base.DESCRIPTION_LEN and out.endswith("\n…")
    kept = out.removesuffix("\n…").split("\n")
    assert kept == lines[:len(kept)] and len(kept) > 200  # whole lines only, nothing half cut
    assert len(base.clean_description("a" * 20000)) == 6000  # one huge line: a hard cut
    assert base.clean_description("a" * 6000) == "a" * 6000
    assert len(base.clean_description("a\n" + "b" * 9000, 100)) == 100  # a line too early to cut at
    assert base.clean_description("word " * 50, None) == ("word " * 50).strip()


def test_clean_description_adversarial_input_is_fast():
    import time
    bodies = [
        "<div>" * 40000 + "x" + "</div>" * 40000,
        "<" * 200_000, ">" * 200_000, "<<>" * 70_000, "<script " * 25_000, "<style a" * 25_000,
        "<p><li>" * 30_000, "</li" + " " * 150_000, "<li " + "a " * 100_000, " " * 200_000,
        "<br>" * 50_000, "\n" * 200_000, "<p>&amp;" * 30_000, "&lt;p&gt;" * 25_000,
        "<ul><li>" + "x" * 100 + "</li>" * 40_000,
    ]
    for body in bodies:
        assert len(body) >= 150_000
        t0 = time.perf_counter()
        out = base.clean_description(body)
        base.clean_description(body, None, plain=True)
        base.make_vacancy(url="https://remotive.com/a", title="T", source="Remotive",
                          domains=("remotive.com",), description=body)
        assert time.perf_counter() - t0 < 1.0, body[:20]
        assert len(out) <= base.DESCRIPTION_LEN


def test_description_raw_input_is_capped_before_regex_work():
    tail = "SECRET-TAIL"
    out = base.clean_description("<p>" + "word " * 5000 + "</p><p>" + tail + "</p>", None)
    assert tail not in out and len(out) <= base._MAX_HTML
    assert tail not in base.clean_description("x" * base._MAX_HTML + tail, None)


def test_snippet_dedupe_key_and_id_do_not_depend_on_description_structure():
    kw = dict(url="https://remotive.com/a", title="Python Dev", company="Acme", location="Berlin",
              source="Remotive", domains=("remotive.com",), snippet=POSTING_HTML)
    flat = base.make_vacancy(**kw, description="Requirements: SQL Python Nice: Tableau")
    rich = base.make_vacancy(**kw, description=POSTING_HTML)
    assert (flat.id, flat.key, flat.snippet) == (rich.id, rich.key, rich.snippet)
    assert flat.to_row() == rich.to_row() and flat.description != rich.description
    assert flat == rich  # description does not take part in equality


def test_remotive_and_dou_keep_structure_from_the_raw_feed():
    (r,) = prov("Remotive").parse(_json({"jobs": [{
        "url": "https://remotive.com/remote-jobs/a-1", "title": "Python Developer",
        "description": POSTING_HTML}]}), "", q(REMOTE))
    assert r.description == POSTING_TEXT
    (d,) = prov("DOU").parse(_rss("https://jobs.dou.ua/companies/a/vacancies/1/", POSTING_HTML,
                                  "Python Developer в Acme, Київ"), "", q(UA))
    assert d.description == POSTING_TEXT and "\n" not in d.snippet
    (j,) = prov("Djinni").parse(_rss("https://djinni.co/jobs/1-x/", POSTING_HTML, "Python Developer"),
                                "", q(UA))
    assert j.description == POSTING_TEXT and "\n" not in j.snippet
    (ji,) = prov("Jobicy").parse(_json({"jobs": [{
        "url": "https://jobicy.com/jobs/1-x", "jobTitle": "Python Developer",
        "jobDescription": POSTING_HTML}]}), "", q(REMOTE))
    assert ji.description == POSTING_TEXT
    (an,) = prov("Arbeitnow").parse(_json({"data": [{
        "title": "Python Developer", "company_name": "Acme", "slug": "x", "description": POSTING_HTML,
        "url": "https://www.arbeitnow.com/jobs/companies/acme/x"}]}), "", q(EU))
    assert an.description == POSTING_TEXT


def test_greenhouse_double_escaped_html_keeps_structure():
    escaped = html.escape(POSTING_HTML)
    (v,) = prov("Greenhouse").parse(_json({"jobs": [{
        "title": "Python Developer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
        "content": escaped}]}), "", q(EU, company="Acme"))
    assert v.description == POSTING_TEXT and "\n" not in v.snippet


def test_plain_text_providers_keep_their_paragraph_breaks():
    plain = "About the role.\n\nYou will:\n- build APIs\n- fix bugs\n\nApply today."
    (jo,) = prov("Jooble").parse(_json({"jobs": [{
        "title": "Python Developer", "link": "https://jooble.org/desc/1", "snippet": plain}]}), "", q(UA))
    (ad,) = prov("Adzuna").parse(_json({"results": [{
        "title": "Python Developer", "redirect_url": "https://www.adzuna.co.uk/jobs/land/ad/1",
        "description": plain}]}), "", q(UK))
    (le,) = prov("Lever").parse(_json([{
        "text": "Python Developer", "hostedUrl": "https://jobs.lever.co/acme/1", "categories": {},
        "descriptionPlain": plain}]), "", q(EU, company="Acme"))
    for v in (jo, ad, le):
        assert v.description == plain
        assert "\n" not in v.snippet and v.snippet.startswith("About the role. You will:")
    # Tags inside the excerpt (Jooble highlights keywords) are dropped, the breaks stay.
    (hl,) = prov("Jooble").parse(_json({"jobs": [{
        "title": "T", "link": "https://jooble.org/desc/2",
        "snippet": "Needs <b>Python</b>&nbsp;skills\nNice: <b>SQL</b>"}]}), "", q(UA))
    assert hl.description == "Needs Python skills\nNice: SQL"


def test_lever_html_fallback_and_lists_get_structure():
    (v,) = prov("Lever").parse(_json([{
        "text": "Python Developer", "hostedUrl": "https://jobs.lever.co/acme/3", "categories": {},
        "description": "<div>Build things.</div><div>Together.</div>",
        "lists": [{"text": "Must have:", "content": "<li>SQL</li><li>Git</li>"},
                  {"text": "Nice to have:", "content": "<li>Tableau</li>"}],
        "additional": "<p>Apply &amp; smile.</p>"}]), "", q(EU, company="Acme"))
    assert v.description == ("Build things.\nTogether.\n\nMust have:\n- SQL\n- Git\n\n"
                             "Nice to have:\n- Tableau\n\nApply & smile.")
