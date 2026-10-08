"""Jooble API (POST JSON, потрібен безплатний ключ JOOBLE_API_KEY)."""
from __future__ import annotations

import re
from collections.abc import Mapping

from cvmax.jobs.models import JobQuery, Vacancy
from cvmax.jobs.providers.base import (
    ALL_REGIONS, EU, REMOTE, UA, UK, US, BaseProvider, as_str, dict_items, env_value,
    allowed_url, make_vacancy, parse_date, parse_json,
)

API = "https://jooble.org/api/"
DOMAINS = ("jooble.org",)
LOCATIONS = {
    UA: ["Ukraine"],
    UK: ["United Kingdom"],
    US: ["United States"],
    EU: ["Germany", "Poland"],
    REMOTE: ["remote"],
}


def _own_link(u: object) -> bool:
    return isinstance(u, str) and u.startswith("https://") and allowed_url(u, DOMAINS)


class Jooble(BaseProvider):
    name = "Jooble"
    kind = "json"
    regions = ALL_REGIONS
    domains = DOMAINS
    needs = ("JOOBLE_API_KEY",)

    def build_requests(self, q: JobQuery, env: Mapping[str, str]) -> list[tuple[str, dict | None]]:
        key = env_value(env, "JOOBLE_API_KEY")
        if not re.fullmatch(r"[A-Za-z0-9-]{8,64}", key):
            return []
        return [
            (f"{API}{key}", {"keywords": q.primary, "location": loc, "page": 1})
            for loc in LOCATIONS.get(q.region, [])
        ]

    def parse(self, body: bytes, url: str, q: JobQuery) -> list[Vacancy]:
        out: list[Vacancy] = []
        for j in dict_items(parse_json(body), "jobs"):
            location = as_str(j.get("location"))
            # Лише власні сторінки Jooble (jooble.org/desc, /away) по https: решта хостів поза allowlist.
            v = make_vacancy(
                url=j.get("link"), title=j.get("title"), company=j.get("company"),
                location=location, source=self.name, posted_at=parse_date(j.get("updated")),
                salary=as_str(j.get("salary")) or None, snippet=j.get("snippet"),
                remote=True if "remote" in location.lower() else None,
                tags=[as_str(j.get("type"))], allow=_own_link,
            )
            if v:
                out.append(v)
        return out


PROVIDER = Jooble()
