"""Robota.ua (rabota.ua): a public API is not confirmed, so search link only."""
from __future__ import annotations

from urllib.parse import quote

from cvmax.jobs.models import JobQuery
from cvmax.jobs.providers.base import UA, BaseProvider


class RobotaUa(BaseProvider):
    name = "Robota.ua"
    kind = "deeplink"
    regions = frozenset({UA})
    domains = ("robota.ua", "rabota.ua")

    def search_url(self, q: JobQuery) -> str | None:
        slug = quote("-".join(q.primary.split()), safe="")
        return f"https://robota.ua/zapros/{slug}/ukraine"


PROVIDER = RobotaUa()
