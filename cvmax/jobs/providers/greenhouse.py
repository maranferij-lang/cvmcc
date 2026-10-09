"""Greenhouse Job Board API (GET JSON without a key) for one company (q.company)."""
from __future__ import annotations

from collections.abc import Mapping

from cvmax.jobs.models import JobQuery, Vacancy
from cvmax.jobs.providers.base import (
    ALL_REGIONS, BaseProvider, as_str, clean_html, company_slug, dict_items, make_vacancy,
    matches_query, parse_date, parse_json,
)

API = "https://boards-api.greenhouse.io/v1/boards/"


class Greenhouse(BaseProvider):
    name = "Greenhouse"
    kind = "json"
    regions = ALL_REGIONS
    domains = ("greenhouse.io", "boards.greenhouse.io", "job-boards.greenhouse.io")

    def build_urls(self, q: JobQuery, env: Mapping[str, str]) -> list[str]:
        slug = company_slug(q)
        # Without content=true: the full HTML of all postings exceeds the response limit (2 MB);
        # we look for a match by title, departments and location.
        return [f"{API}{slug}/jobs"] if slug else []

    def parse(self, body: bytes, url: str, q: JobQuery) -> list[Vacancy]:
        out: list[Vacancy] = []
        for j in dict_items(parse_json(body), "jobs"):
            loc = j.get("location") if isinstance(j.get("location"), dict) else {}
            depts = [as_str(d.get("name")) for d in dict_items(j.get("departments"))]
            content = clean_html(j.get("content"), None)
            if not matches_query(q, as_str(j.get("title")), " ".join(depts), content):
                continue
            where = as_str(loc.get("name"))
            meta = " ".join(as_str(m.get("value")) for m in dict_items(j.get("metadata")))
            remote = True if "remote" in (where + " " + meta).lower() else None
            v = make_vacancy(
                url=j.get("absolute_url"), title=j.get("title"),
                company=j.get("company_name") or q.company, location=where,
                source=self.name, domains=self.domains,
                posted_at=parse_date(j.get("first_published") or j.get("updated_at")),
                snippet=content, remote=remote, tags=depts,
            )
            if v:
                out.append(v)
        return out


PROVIDER = Greenhouse()
