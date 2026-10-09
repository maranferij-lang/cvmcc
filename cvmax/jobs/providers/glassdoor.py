"""Glassdoor: search link only (parameters from memory, low confidence)."""
from __future__ import annotations

from urllib.parse import quote_plus, urlencode

from cvmax.jobs.models import JobQuery
from cvmax.jobs.providers.base import EU, REMOTE, UK, US, BaseProvider

LOCATIONS = {UK: "United Kingdom", US: "United States", EU: "Germany", REMOTE: "remote"}


class Glassdoor(BaseProvider):
    name = "Glassdoor"
    kind = "deeplink"
    regions = frozenset(LOCATIONS)
    domains = ("glassdoor.com",)

    def search_url(self, q: JobQuery) -> str | None:
        loc = LOCATIONS.get(q.region)
        if not loc:
            return None
        params = {"sc.keyword": q.primary, "locKeyword": loc}
        return "https://www.glassdoor.com/Job/jobs.htm?" + urlencode(params, quote_via=quote_plus)


PROVIDER = Glassdoor()
