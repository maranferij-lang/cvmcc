"""Djinni: RSS-фід (підтверджено лише primary_keyword). Посилання і поля з опису потребують
перевірки на реальному фіді; елементи без дозволеного посилання відкидаються."""
from __future__ import annotations

import re
from collections.abc import Mapping

from cvmax.jobs.models import JobQuery, Vacancy
from cvmax.jobs.providers.base import (
    REMOTE, UA, BaseProvider, clean_html, make_vacancy, parse_date, parse_rss, quote_kw,
)

FEED = "https://djinni.co/jobs/rss/"
SEARCH = "https://djinni.co/jobs/"
_NEXT_FIELD = r"(?=\.?\s+(?:Position|Location|Employment Type|Salary|Company|Experience|English)\s*:|\.\s|$)"


def _field(text: str, label: str) -> str:
    m = re.search(rf"{label}\s*:\s*(.+?){_NEXT_FIELD}", text)
    return m.group(1).strip() if m else ""


class Djinni(BaseProvider):
    name = "Djinni"
    kind = "rss"
    regions = frozenset({UA, REMOTE})
    domains = ("djinni.co",)

    def build_urls(self, q: JobQuery, env: Mapping[str, str]) -> list[str]:
        if q.region not in self.regions:
            return []
        return [f"{FEED}?primary_keyword={quote_kw(kw)}" for kw in q.keywords[:2]]

    def parse(self, body: bytes, url: str, q: JobQuery) -> list[Vacancy]:
        out: list[Vacancy] = []
        for it in parse_rss(body):
            text = clean_html(it["description"], None)
            location = _field(text, "Location")
            v = make_vacancy(
                url=it["link"], title=it["title"], company=_field(text, "Company"),
                location=location, source=self.name, domains=self.domains,
                posted_at=parse_date(it["pubDate"]), salary=_field(text, "Salary") or None,
                snippet=text, remote=True if "remote" in location.lower() else None,
            )
            if v:
                out.append(v)
        return out

    def search_url(self, q: JobQuery) -> str | None:
        return f"{SEARCH}?primary_keyword={quote_kw(q.primary)}"


PROVIDER = Djinni()
