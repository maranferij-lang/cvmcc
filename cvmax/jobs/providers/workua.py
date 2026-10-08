"""Work.ua: лише посилання на пошук (публічного фіда чи API немає, скрейпінг не робимо)."""
from __future__ import annotations

from cvmax.jobs.models import JobQuery
from cvmax.jobs.providers.base import UA, BaseProvider, quote_kw


class WorkUa(BaseProvider):
    name = "Work.ua"
    kind = "deeplink"
    regions = frozenset({UA})
    domains = ("work.ua",)

    def search_url(self, q: JobQuery) -> str | None:
        return f"https://www.work.ua/jobs/?search={quote_kw(q.primary)}"


PROVIDER = WorkUa()
