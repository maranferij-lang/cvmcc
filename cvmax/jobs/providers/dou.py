"""DOU (jobs.dou.ua): публічний RSS-фід вакансій, параметри search, remote, exp."""
from __future__ import annotations

import re
from collections.abc import Mapping

from cvmax.jobs.models import JobQuery, Vacancy
from cvmax.jobs.providers.base import (
    REMOTE, UA, BaseProvider, allowed_url, clean_html, make_vacancy, parse_date,
    parse_rss, quote_kw,
)

FEED = "https://jobs.dou.ua/vacancies/feeds/"
SEARCH = "https://jobs.dou.ua/vacancies/"
_SALARY = re.compile(r"[$€£]|грн|uah|usd|eur", re.I)
_REMOTE_WORDS = ("віддалено", "remote")


def split_title(title: str) -> tuple[str, str, str, str | None, bool]:
    """'Role в Company, $sal, City, віддалено' -> (role, company, location, salary, remote)."""
    head, sep, rest = title.rpartition(" в ")
    role = head.strip() if sep else title.strip()
    parts = [p.strip() for p in rest.split(",") if p.strip()] if sep else []
    company = parts[0] if parts else ""
    salary = None
    remote = False
    places: list[str] = []
    for part in parts[1:]:
        if part.lower() in _REMOTE_WORDS:
            remote = True
        elif _SALARY.search(part) and salary is None:
            salary = part
        else:
            places.append(part)
    location = ", ".join(places) or ("Remote" if remote else "")
    return role, company, location, salary, remote


class Dou(BaseProvider):
    name = "DOU"
    kind = "rss"
    regions = frozenset({UA, REMOTE})
    domains = ("jobs.dou.ua", "dou.ua")

    def _flags(self, q: JobQuery) -> str:
        flags = ""
        if q.remote_ok or q.region == REMOTE:
            flags += "&remote"
        if q.wants_entry_level:
            flags += "&exp=0-1"  # підтверджено дослідженням
        return flags

    def build_urls(self, q: JobQuery, env: Mapping[str, str]) -> list[str]:
        if q.region not in self.regions:
            return []
        return [f"{FEED}?search={quote_kw(kw)}{self._flags(q)}" for kw in q.keywords[:2]]

    def parse(self, body: bytes, url: str, q: JobQuery) -> list[Vacancy]:
        out: list[Vacancy] = []
        for it in parse_rss(body):
            role, company, location, salary, remote = split_title(clean_html(it["title"], None))
            v = make_vacancy(
                url=it["link"], title=role, company=company, location=location,
                source=self.name, domains=self.domains, posted_at=parse_date(it["pubDate"]),
                salary=salary, snippet=it["description"], remote=remote,
            )
            if v:
                out.append(v)
        return out

    def search_url(self, q: JobQuery) -> str | None:
        return f"{SEARCH}?search={quote_kw(q.primary)}{self._flags(q)}"


PROVIDER = Dou()
__all__ = ["PROVIDER", "Dou", "split_title", "allowed_url"]
