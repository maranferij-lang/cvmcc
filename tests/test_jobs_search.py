"""Тести пошуку й ранжування вакансій (без мережі)."""
from __future__ import annotations

from datetime import date

import pytest

from cvmax.demo import FakeClient
from cvmax.jobs import JobQuery, Vacancy, demo_search_result, rank_jobs, search_jobs
from cvmax.jobs.providers import ALL
from cvmax.jobs.providers.base import allowed_url
from cvmax.jobs.search import dedupe, filter_level, prerank

TODAY = date(2026, 10, 8)


def vac(title, url=None, company="Acme", snippet="", posted="2026-10-07", source="X"):
    url = url or f"https://example.com/{abs(hash((title, company)))}"
    return Vacancy(url=url, title=title, company=company, location="", source=source,
                   posted_at=posted, salary=None, snippet=snippet, remote=None)


def query(level="Internship"):
    return JobQuery(keywords=("Data Analyst",), region="UK", level=level)


class FakeProvider:
    kind = "json"
    regions = frozenset({"UK"})
    domains = ("example.com",)
    needs = ()

    def __init__(self, name, items=None, boom=False):
        self.name, self.items, self.boom = name, items or [], boom

    def build_urls(self, q, env):
        return [f"https://example.com/{self.name}"]

    def parse(self, body, url, q):
        if self.boom:
            raise RuntimeError("bad")
        return list(self.items)

    def search_url(self, q):
        return None


def test_dedupe_by_url_and_key_keeps_first():
    a = vac("Data Analyst", url="https://example.com/1", source="DOU")
    b = vac("Other", url="https://example.com/1", source="Jooble")
    c = vac("data analyst!", url="https://example.com/3", source="Jooble")
    d = vac("Data Analyst", url="https://example.com/4", company="Other Co")
    out = dedupe([a, b, c, d])
    assert out == [a, d]


def test_filter_level():
    s, j = vac("Senior Data Analyst"), vac("Data Analyst Intern")
    assert filter_level([s, j], query("Internship")) == [j]
    assert filter_level([s, j], query("Mid-level")) == [s, j]
    q = JobQuery(keywords=("Senior Analyst",), region="UK", level="Internship")
    assert filter_level([s, j], q) == [s, j]


def test_prerank_title_over_snippet_and_fresh_over_old():
    title = vac("Data Analyst", posted=None)
    snippet = vac("Clerk", snippet="data analyst tasks", posted=None)
    assert prerank([snippet, title], query(), today=TODAY)[0] is title
    old = vac("Data Analyst", company="A", posted="2026-01-01")
    fresh = vac("Data Analyst", company="B", posted="2026-10-05")
    assert prerank([old, fresh], query(), today=TODAY)[0] is fresh


def test_failing_provider_goes_to_failed():
    good = FakeProvider("good", [vac("Data Analyst Intern", url="https://example.com/g")])
    bad = FakeProvider("bad", boom=True)
    empty = FakeProvider("empty")
    res = search_jobs(query(), env={}, providers=[good, bad, empty],
                      fetch_many=lambda reqs: [b"x" if "empty" not in u else None for u, _ in reqs], today=TODAY)
    assert [v.title for v in res.vacancies] == ["Data Analyst Intern"]
    assert res.sources_ok == ["good"]
    assert sorted(res.sources_failed) == ["bad", "empty"]


def test_no_providers_returns_deep_links():
    res = search_jobs(query(), env={}, providers=[])
    assert res.vacancies == [] and res.sources_ok == []
    assert res.deep_links


def test_fetch_many_crash_never_raises():
    def boom(reqs):
        raise RuntimeError("net")
    res = search_jobs(query(), env={}, providers=[FakeProvider("a")], fetch_many=boom)
    assert res.vacancies == []


def test_rank_jobs_merges_clamps_sorts():
    vs = [vac("A", url="https://example.com/a"), vac("B", url="https://example.com/b", company="B"),
          vac("C", url="https://example.com/c", company="C")]
    client = FakeClient()
    pairs = rank_jobs(client, cv_text="cv" * 5000, role="Analyst", gaps=["SQL"], keywords=["Analyst"],
                      vacancies=vs, feedback_language="English")
    assert [p[0] for p in pairs] == vs
    assert [p[1].fit for p in pairs] == [85, 72, 64]
    assert pairs[1][1].missing == ["SQL"] and pairs[2][1].apply_now
    assert len(str(client.calls[0]["messages"])) < 12000


def test_rank_jobs_clamp_unknown_and_missing(monkeypatch):
    from cvmax.schemas import JobFit, RankedJobs
    vs = [vac("A", url="https://example.com/a"), vac("B", url="https://example.com/b", company="B"),
          vac("C", url="https://example.com/c", company="C")]
    out = RankedJobs(items=[
        JobFit(id=vs[2].id, fit=150, why="w", missing=[], apply_now=True),
        JobFit(id=vs[0].id, fit=-5, why="w", missing=[], apply_now=False),
        JobFit(id="ffffffffffff", fit=99, why="w", missing=[], apply_now=True),
    ])
    monkeypatch.setattr("cvmax.jobs.rank.ask_structured", lambda *a, **k: out)
    pairs = rank_jobs(None, cv_text="", role="r", gaps=[], keywords=["k"], vacancies=vs, feedback_language="English")
    assert [(p[0].title, p[1].fit if p[1] else None) for p in pairs] == [("C", 100), ("A", 0), ("B", None)]


def test_demo_search_result_urls_allowed():
    res = demo_search_result(query())
    assert len(res.vacancies) == 6
    domains = {d for p in ALL for d in p.domains}
    for v in res.vacancies:
        assert any(allowed_url(v.url, p.domains) for p in ALL), v.url
    assert res.deep_links and res.sources_failed == []
    assert domains
