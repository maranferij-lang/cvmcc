"""Tests of the job providers: parsers on fixtures, allowlist, HTTP layer, models."""
from __future__ import annotations

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
