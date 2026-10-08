"""Реєстр провайдерів вакансій і вибір за регіоном та налаштуваннями."""
from __future__ import annotations

from collections.abc import Mapping

from cvmax.jobs.models import JobQuery
from cvmax.jobs.providers import (
    adzuna, arbeitnow, djinni, dou, glassdoor, greenhouse, indeed, jobicy, jooble, lever,
    linkedin, remotive, robotaua, workua,
)
from cvmax.jobs.providers.base import Provider, has_needs, requests_for

# Спочатку джерела з відкритим доступом, потім ключові, потім посилання.
ALL: list[Provider] = [
    dou.PROVIDER, djinni.PROVIDER, jooble.PROVIDER, adzuna.PROVIDER, arbeitnow.PROVIDER,
    remotive.PROVIDER, jobicy.PROVIDER, greenhouse.PROVIDER, lever.PROVIDER,
    linkedin.PROVIDER, workua.PROVIDER, robotaua.PROVIDER, indeed.PROVIDER,
    glassdoor.PROVIDER,
]

# Порядок посилань: LinkedIn, місцеві сайти (UA), потім Indeed і Glassdoor.
_LINK_ORDER = ("LinkedIn", "DOU", "Djinni", "Work.ua", "Robota.ua", "Indeed", "Glassdoor")


def for_region(region: str) -> list[Provider]:
    return [p for p in ALL if region in p.regions]


def deep_links(q: JobQuery) -> list[tuple[str, str]]:
    """(назва, адреса) для провайдерів, що мають сторінку пошуку в цьому регіоні."""
    found = {}
    for p in for_region(q.region):
        url = p.search_url(q)
        if url:
            found[p.name] = url
    names = [n for n in _LINK_ORDER if n in found] + [n for n in found if n not in _LINK_ORDER]
    return [(n, found[n]) for n in names]


def fetchers(q: JobQuery, env: Mapping[str, str]) -> list[Provider]:
    """Провайдери, які реально завантажують вакансії: регіон, ключі й запити в порядку."""
    return [
        p for p in for_region(q.region)
        if p.kind != "deeplink" and has_needs(p, env) and requests_for(p, q, env)
    ]


__all__ = ["ALL", "for_region", "deep_links", "fetchers"]
