"""Jobicy API v2 (GET JSON без ключа). Умови: вказувати Jobicy, лишати їхнє посилання,
опитувати не частіше ніж раз на годину (кеш 30 хв)."""
from __future__ import annotations

from collections.abc import Mapping

from cvmax.jobs.models import JobQuery, Vacancy
from cvmax.jobs.providers.base import (
    REMOTE, BaseProvider, as_str, dict_items, make_vacancy, parse_date, parse_json, quote_kw,
)

API = "https://jobicy.com/api/v2/remote-jobs"


def _salary(j: dict) -> str | None:
    lo, hi = j.get("salaryMin"), j.get("salaryMax")
    nums = [int(x) for x in (lo, hi) if isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0]
    if not nums:
        return None
    cur = as_str(j.get("salaryCurrency"))
    span = f"{nums[0]:,}–{nums[1]:,}" if len(nums) == 2 and nums[0] != nums[1] else f"{nums[0]:,}"
    period = as_str(j.get("salaryPeriod"))
    return f"{cur} {span}".strip() + (f" {period}" if period else "")


def _list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    return [x for x in value if isinstance(x, str)] if isinstance(value, list) else []


class Jobicy(BaseProvider):
    name = "Jobicy"
    kind = "json"
    regions = frozenset({REMOTE})
    domains = ("jobicy.com",)

    def build_urls(self, q: JobQuery, env: Mapping[str, str]) -> list[str]:
        if q.region not in self.regions:
            return []
        return [f"{API}?count=50&tag={quote_kw(q.primary)}"]

    def parse(self, body: bytes, url: str, q: JobQuery) -> list[Vacancy]:
        out: list[Vacancy] = []
        for j in dict_items(parse_json(body), "jobs"):
            v = make_vacancy(
                url=j.get("url"), title=j.get("jobTitle"), company=j.get("companyName"),
                location=j.get("jobGeo"), source=self.name, domains=self.domains,
                posted_at=parse_date(j.get("pubDate")), salary=_salary(j),
                snippet=j.get("jobExcerpt") or j.get("jobDescription"), remote=True,
                tags=_list(j.get("jobIndustry")) + _list(j.get("jobType")),
            )
            if v:
                out.append(v)
        return out


PROVIDER = Jobicy()
