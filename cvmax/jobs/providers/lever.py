"""Lever Postings API (GET JSON without a key) for one company (q.company)."""
from __future__ import annotations

from collections.abc import Mapping

from cvmax.jobs.models import JobQuery, Vacancy
from cvmax.jobs.providers.base import (
    ALL_REGIONS, BaseProvider, as_str, company_slug, dict_items, make_vacancy, matches_query,
    parse_date, parse_json,
)

API = "https://api.lever.co/v0/postings/"


def _salary(j: dict) -> str | None:
    r = j.get("salaryRange")
    if not isinstance(r, dict):
        return None
    lo, hi = r.get("min"), r.get("max")
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (lo, hi)):
        return None
    return f"{as_str(r.get('currency'))} {int(lo):,}–{int(hi):,}".strip()


def _full_text(j: dict, plain: str) -> str:
    """The opening text, then every list section (title and HTML items: the requirements usually
    live there) and the closing text. Cleaned later by make_vacancy: blocks are separated by a blank
    line, a section title stays on the line above its items."""
    blocks = [plain or as_str(j.get("description"))]
    for section in dict_items(j.get("lists")):
        blocks.append(" ".join(p for p in (as_str(section.get("text")), as_str(section.get("content")))
                               if p.strip()))
    blocks.append(as_str(j.get("additionalPlain")) or as_str(j.get("additional")))
    # Plain text separates paragraphs with newlines; in HTML source newlines are only whitespace.
    return ("\n\n" if plain else "<br><br>").join(b for b in blocks if b.strip())


class Lever(BaseProvider):
    name = "Lever"
    kind = "json"
    regions = ALL_REGIONS
    domains = ("lever.co", "jobs.lever.co")

    def build_urls(self, q: JobQuery, env: Mapping[str, str]) -> list[str]:
        slug = company_slug(q)
        return [f"{API}{slug}?mode=json&limit=100"] if slug else []

    def parse(self, body: bytes, url: str, q: JobQuery) -> list[Vacancy]:
        data = parse_json(body)
        out: list[Vacancy] = []
        for j in dict_items(data):
            cats = j.get("categories") if isinstance(j.get("categories"), dict) else {}
            plain = as_str(j.get("descriptionPlain"))
            team = as_str(cats.get("team")) or as_str(cats.get("department"))
            if not matches_query(q, as_str(j.get("text")), team, plain):
                continue
            where = as_str(cats.get("location"))
            wt = as_str(j.get("workplaceType")).lower()
            remote = True if wt == "remote" or "remote" in where.lower() else None
            v = make_vacancy(
                url=j.get("hostedUrl"), title=j.get("text"), company=company_slug(q) or q.company,
                location=where, source=self.name, domains=self.domains,
                posted_at=parse_date(j.get("createdAt")), salary=_salary(j),
                snippet=plain, remote=remote, tags=[team, as_str(cats.get("commitment"))],
                description=_full_text(j, plain), description_plain=bool(plain),
            )
            if v:
                out.append(v)
        return out


PROVIDER = Lever()
