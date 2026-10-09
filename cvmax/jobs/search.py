"""Job search routing: requests to sources, deduplication, level filter, ranking."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Mapping

from cvmax import config
from cvmax.jobs.models import JobQuery, Vacancy
from cvmax.jobs.providers import deep_links, fetchers
from cvmax.jobs.providers.base import requests_for

SENIOR_RE = re.compile(
    r"\b(senior|lead|head\s+of|principal|architect|director|chief|staff\s+(?:engineer|software|developer|scientist)|manager)\b", re.IGNORECASE)
ENTRY_RE = re.compile(
    r"\b(intern|internship|trainee|junior|graduate|entry|стажер|стажування|молодший)\b",
    re.IGNORECASE)


@dataclass
class SearchResult:
    vacancies: list[Vacancy] = field(default_factory=list)
    sources_ok: list[str] = field(default_factory=list)
    sources_failed: list[str] = field(default_factory=list)
    deep_links: list[tuple[str, str]] = field(default_factory=list)


def _has_word(text: str, word: str) -> bool:
    """A whole word if the word consists of letters/digits; otherwise a simple substring check."""
    low, w = text.lower(), word.lower().strip()
    if not w:
        return False
    if re.fullmatch(r"[\w ]+", w):
        return re.search(rf"(?<!\w){re.escape(w)}(?!\w)", low) is not None
    return w in low


def _age_days(posted_at: str | None, today: date) -> int | None:
    try:
        return (today - datetime.strptime(str(posted_at)[:10], "%Y-%m-%d").date()).days
    except (ValueError, TypeError):
        return None


def _score(v: Vacancy, q: JobQuery, today: date) -> int:
    score = 0
    for kw in q.keywords:
        if _has_word(v.title, kw):
            score += 3
        if _has_word(v.snippet, kw):
            score += 1
    if q.wants_entry_level and ENTRY_RE.search(v.title):
        score += 2
    age = _age_days(v.posted_at, today)
    if age is not None:
        if age <= 7:
            score += 2
        elif age <= 30:
            score += 1
    return score


def prerank(vacancies: list[Vacancy], q: JobQuery, *, today: date | None = None) -> list[Vacancy]:
    """Sorts by word match and freshness (stable), truncates to JOBS_MAX_RESULTS."""
    day = today or date.today()
    ranked = sorted(vacancies, key=lambda v: -_score(v, q, day))
    return ranked[: config.JOBS_MAX_RESULTS]


def dedupe(vacancies: list[Vacancy]) -> list[Vacancy]:
    """First by url, then by title|company; the first one stays (provider order matters)."""
    urls: set[str] = set()
    keys: set[str] = set()
    out: list[Vacancy] = []
    for v in vacancies:
        if v.url in urls or v.key in keys:
            continue
        urls.add(v.url)
        keys.add(v.key)
        out.append(v)
    return out


def filter_level(vacancies: list[Vacancy], q: JobQuery) -> list[Vacancy]:
    """For student levels removes senior roles, unless the query itself asked for them."""
    if not q.wants_entry_level or any(SENIOR_RE.search(k) for k in q.keywords):
        return list(vacancies)
    # Entry-level markers (Junior/Intern/...) take precedence over 'manager' and the like.
    return [v for v in vacancies if ENTRY_RE.search(v.title) or not SENIOR_RE.search(v.title)]


def _default_fetch(reqs: list[tuple[str, Any]]) -> list[bytes | None]:
    from cvmax.jobs.http import fetch_requests
    return fetch_requests(reqs, workers=config.JOBS_WORKERS)


def search_jobs(
    q: JobQuery,
    *,
    env: Mapping[str, str] | None = None,
    fetch_many: Callable[[list[tuple[str, Any]]], list[bytes | None]] | None = None,
    providers: list | None = None,
    today: date | None = None,
) -> SearchResult:
    """Searches for job postings in parallel in all available sources. Never raises an exception.

    fetch_many takes a list of (url, json_body | None) and returns a list of bodies
    (bytes | None) in the same order; by default http.fetch_requests.
    """
    result = SearchResult()
    try:
        result.deep_links = deep_links(q)
    except Exception:
        pass
    try:
        environ = os.environ if env is None else env
        chosen = list(providers) if providers is not None else fetchers(q, environ)
        plans: list[tuple[Any, list[tuple[str, Any]]]] = []
        for p in chosen:
            try:
                plans.append((p, list(requests_for(p, q, environ))))
            except Exception:
                result.sources_failed.append(getattr(p, "name", "?"))
        flat = [r for _, reqs in plans for r in reqs]
        bodies = list((fetch_many or _default_fetch)(flat)) if flat else []
        bodies += [None] * (len(flat) - len(bodies))
        found: list[Vacancy] = []
        pos = 0
        for p, reqs in plans:
            part = bodies[pos:pos + len(reqs)]
            pos += len(reqs)
            try:
                items: list[Vacancy] = []
                got_any = False
                for (url, _), body in zip(reqs, part):
                    if body:
                        got_any = True
                        items.extend(p.parse(body, url, q))
                (result.sources_ok if got_any else result.sources_failed).append(p.name)
                found.extend(items)
            except Exception:
                result.sources_failed.append(p.name)
        result.vacancies = prerank(filter_level(dedupe(found), q), q, today=today)
    except Exception:
        result.vacancies = []
    return result
