"""LinkedIn: лише посилання на пошук. Автоматичний доступ заборонено Угодою користувача."""
from __future__ import annotations

from urllib.parse import quote_plus, urlencode

from cvmax.jobs.models import JobQuery
from cvmax.jobs.providers.base import ALL_REGIONS, EU, REMOTE, UA, UK, US, BaseProvider

LOCATIONS = {UA: "Ukraine", UK: "United Kingdom", US: "United States",
             EU: "European Union", REMOTE: "Worldwide"}


class LinkedIn(BaseProvider):
    name = "LinkedIn"
    kind = "deeplink"
    regions = ALL_REGIONS
    domains = ("linkedin.com",)

    def search_url(self, q: JobQuery) -> str | None:
        params = {"keywords": q.primary, "location": LOCATIONS.get(q.region, "Worldwide")}
        if q.wants_entry_level:
            params["f_E"] = "1,2"
        params["f_TPR"] = "r2592000"
        if q.remote_ok or q.region == REMOTE:
            params["f_WT"] = "2"
        return "https://www.linkedin.com/jobs/search/?" + urlencode(params, quote_via=quote_plus)


PROVIDER = LinkedIn()
