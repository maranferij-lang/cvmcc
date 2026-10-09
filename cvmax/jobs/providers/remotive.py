"""Remotive (GET JSON without a key). Terms: link to remotive.com and name the source;
do not pass postings to third-party aggregators; no more than ~4 requests per day (cache 30 min)."""
from __future__ import annotations

from collections.abc import Mapping

from cvmax.jobs.models import JobQuery, Vacancy
from cvmax.jobs.providers.base import (
    ALL_REGIONS, REMOTE, BaseProvider, as_str, dict_items, make_vacancy, parse_date,
    parse_json, quote_kw,
)

API = "https://remotive.com/api/remote-jobs"


class Remotive(BaseProvider):
    name = "Remotive"
    kind = "json"
    regions = ALL_REGIONS
    domains = ("remotive.com", "remotive.io")

    def build_urls(self, q: JobQuery, env: Mapping[str, str]) -> list[str]:
        if q.region != REMOTE and not q.remote_ok:
            return []
        return [f"{API}?search={quote_kw(q.primary)}&limit=50"]

    def parse(self, body: bytes, url: str, q: JobQuery) -> list[Vacancy]:
        out: list[Vacancy] = []
        for j in dict_items(parse_json(body), "jobs"):
            v = make_vacancy(
                url=j.get("url"), title=j.get("title"), company=j.get("company_name"),
                location=j.get("candidate_required_location"), source=self.name,
                domains=self.domains, posted_at=parse_date(j.get("publication_date")),
                salary=as_str(j.get("salary")) or None, snippet=j.get("description"),
                remote=True, tags=[as_str(j.get("category")), as_str(j.get("job_type"))],
            )
            if v:
                out.append(v)
        return out


PROVIDER = Remotive()
