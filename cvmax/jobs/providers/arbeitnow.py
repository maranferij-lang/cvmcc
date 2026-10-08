"""Arbeitnow (GET JSON без ключа). Пошук на сервері неточний, тому фільтруємо локально."""
from __future__ import annotations

from collections.abc import Mapping

from cvmax.jobs.models import JobQuery, Vacancy
from cvmax.jobs.providers.base import (
    EU, REMOTE, BaseProvider, as_str, clean_html, dict_items, make_vacancy, matches_query,
    parse_date, parse_json, quote_kw,
)

API = "https://www.arbeitnow.com/api/job-board-api"


class Arbeitnow(BaseProvider):
    name = "Arbeitnow"
    kind = "json"
    regions = frozenset({EU, REMOTE})
    domains = ("arbeitnow.com",)

    def build_urls(self, q: JobQuery, env: Mapping[str, str]) -> list[str]:
        if q.region not in self.regions:
            return []
        return [f"{API}?search={quote_kw(q.primary)}"]

    def parse(self, body: bytes, url: str, q: JobQuery) -> list[Vacancy]:
        out: list[Vacancy] = []
        for j in dict_items(parse_json(body), "data"):
            tags = [t for t in (j.get("tags") or []) if isinstance(t, str)]
            types = [t for t in (j.get("job_types") or []) if isinstance(t, str)]
            remote = j.get("remote") if isinstance(j.get("remote"), bool) else None
            if q.region == REMOTE and remote is not True:
                continue
            if not matches_query(q, as_str(j.get("title")), " ".join(tags),
                                 clean_html(j.get("description"), 2000)):
                continue
            v = make_vacancy(
                url=j.get("url"), title=j.get("title"), company=j.get("company_name"),
                location=j.get("location"), source=self.name, domains=self.domains,
                posted_at=parse_date(j.get("created_at")), snippet=j.get("description"),
                remote=remote, tags=tags + types,
            )
            if v:
                out.append(v)
        return out


PROVIDER = Arbeitnow()
