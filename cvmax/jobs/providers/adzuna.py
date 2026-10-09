"""Adzuna API (GET JSON, keys ADZUNA_APP_ID and ADZUNA_APP_KEY). Does not cover Ukraine."""
from __future__ import annotations

import re
from collections.abc import Mapping

from cvmax.jobs.models import JobQuery, Vacancy
from cvmax.jobs.providers.base import (
    EU, UK, US, BaseProvider, allowed_url, as_str, dict_items, env_value, make_vacancy, parse_date,
    parse_json, quote_kw,
)

API = "https://api.adzuna.com/v1/api/jobs"
COUNTRIES = {UK: ["gb"], US: ["us", "ca"], EU: ["de", "pl", "nl"]}
CURRENCY = {"gb": "£", "us": "$", "ca": "CA$", "de": "€", "nl": "€", "pl": "PLN "}
# Only real Adzuna domains (not any registered adzuna.<tld>).
_ADZUNA_DOMAINS = ("adzuna.co.uk", "adzuna.com", "adzuna.com.au", "adzuna.ca", "adzuna.de", "adzuna.pl", "adzuna.nl")
_CRED = re.compile(r"[A-Za-z0-9_-]{3,64}")


def adzuna_url_ok(url: object) -> bool:
    return allowed_url(url, _ADZUNA_DOMAINS)


def _money(lo: object, hi: object, cur: str) -> str | None:
    nums = [int(x) for x in (lo, hi) if isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0]
    if not nums:
        return None
    if len(nums) == 2 and nums[0] != nums[1]:
        return f"{cur}{nums[0]:,}–{nums[1]:,}"
    return f"{cur}{nums[0]:,}"


class Adzuna(BaseProvider):
    name = "Adzuna"
    kind = "json"
    regions = frozenset(COUNTRIES)
    domains = ("api.adzuna.com",)
    needs = ("ADZUNA_APP_ID", "ADZUNA_APP_KEY")

    def build_urls(self, q: JobQuery, env: Mapping[str, str]) -> list[str]:
        app_id, app_key = env_value(env, "ADZUNA_APP_ID"), env_value(env, "ADZUNA_APP_KEY")
        if not (_CRED.fullmatch(app_id) and _CRED.fullmatch(app_key)):
            return []
        return [
            f"{API}/{cc}/search/1?app_id={app_id}&app_key={app_key}&what={quote_kw(q.primary)}"
            "&results_per_page=20&max_days_old=30&sort_by=date&content-type=application/json"
            for cc in COUNTRIES.get(q.region, [])
        ]

    def parse(self, body: bytes, url: str, q: JobQuery) -> list[Vacancy]:
        m = re.search(r"/jobs/([a-z]{2})/search", url or "")
        cur = CURRENCY.get(m.group(1), "") if m else ""
        out: list[Vacancy] = []
        for j in dict_items(parse_json(body), "results"):
            company = j.get("company") if isinstance(j.get("company"), dict) else {}
            loc = j.get("location") if isinstance(j.get("location"), dict) else {}
            predicted = str(j.get("salary_is_predicted")) in ("1", "True", "true")
            salary = None if predicted else _money(j.get("salary_min"), j.get("salary_max"), cur)
            v = make_vacancy(
                url=j.get("redirect_url"), title=j.get("title"),
                company=company.get("display_name"), location=loc.get("display_name"),
                source=self.name, posted_at=parse_date(j.get("created")), salary=salary,
                snippet=j.get("description"), allow=adzuna_url_ok,
                tags=[as_str(j.get("contract_time"))],
            )
            if v:
                out.append(v)
        return out


PROVIDER = Adzuna()
