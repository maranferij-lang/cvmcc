"""Indeed: search link only. Ukraine has no separate site, so the UA region is not covered."""
from __future__ import annotations

from urllib.parse import quote_plus, urlencode

from cvmax.jobs.models import JobQuery
from cvmax.jobs.providers.base import EU, REMOTE, UK, US, BaseProvider

SITES = {UK: ("uk.indeed.com", "United Kingdom"), US: ("www.indeed.com", "United States"),
         EU: ("de.indeed.com", ""), REMOTE: ("www.indeed.com", "remote")}


class Indeed(BaseProvider):
    name = "Indeed"
    kind = "deeplink"
    regions = frozenset(SITES)
    domains = ("indeed.com",)

    def search_url(self, q: JobQuery) -> str | None:
        site = SITES.get(q.region)
        if not site:
            return None
        host, loc = site
        params = {"q": q.primary}
        if loc:
            params["l"] = loc
        params.update({"fromage": "30", "sort": "date"})
        return f"https://{host}/jobs?" + urlencode(params, quote_via=quote_plus)


PROVIDER = Indeed()
