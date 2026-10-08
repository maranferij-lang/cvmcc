"""Щоденний знімок вакансій: запити початкового рівня за напрямами й регіонами -> cvmax_vacancies у Supabase.

Запуск:  python scripts/snapshot_jobs.py
         python scripts/snapshot_jobs.py --dry-run --programs law other
Потрібні змінні: SUPABASE_URL, SUPABASE_KEY, CVMAX_DB_TOKEN (необов'язково JOOBLE_API_KEY, ADZUNA_*).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cvmax.db import SupabaseDB  # noqa: E402
from cvmax.jobs.models import JobQuery  # noqa: E402
from cvmax.learning.market import REGIONS_TO_SNAPSHOT, ROLE_QUERIES  # noqa: E402

CHUNK = 100
# Ці джерела мають жорсткі ліміти (Remotive ~4 запити на добу, Jobicy раз на годину):
# щоденний знімок їх не чіпає, вони обслуговують лише живу вкладку Jobs.
RATE_LIMITED = frozenset({"remotive", "jobicy"})


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="не писати в базу, лише порахувати")
    ap.add_argument("--programs", nargs="*", default=None, help="обмежити напрями")
    return ap.parse_args(argv)


def _count(value: Any) -> int:
    try:
        return len(value)
    except TypeError:
        return int(value or 0)


def to_rows(vacancies: list, region: str, query: str) -> list[dict]:
    return [{**v.to_row(), "region": region, "role_family": query, "query": query} for v in vacancies]


def upsert_chunks(db: Any, rows: list[dict]) -> int:
    return sum(db.upsert_vacancies(rows[i:i + CHUNK]) for i in range(0, len(rows), CHUNK))


def run_snapshot(db: Any, search: Callable[[JobQuery], Any], programs: list[str], regions: list[str],
                 sleep: float = 0.5, dry_run: bool = False) -> list[dict]:
    """Проходить усі пари напрям x запит x регіон. Повертає рядки підсумку по (напрям, регіон)."""
    summary: dict[tuple[str, str], dict] = {}
    first = True
    for program in programs:
        for query in ROLE_QUERIES.get(program, []):
            for region in regions:
                if not first and sleep:
                    time.sleep(sleep)
                first = False
                row = summary.setdefault((program, region), {"program": program, "region": region,
                                                             "fetched": 0, "upserted": 0, "failed": 0})
                try:
                    q = JobQuery(keywords=(query,), region=region, level="Internship")
                    result = search(q)
                    rows = to_rows(list(result.vacancies), region, query)
                    row["failed"] += _count(getattr(result, "sources_failed", ()))
                    row["fetched"] += len(rows)
                    if rows and not dry_run:
                        row["upserted"] += upsert_chunks(db, rows)
                except Exception as e:  # один збій не зупиняє знімок
                    row["failed"] += 1
                    print(f"search failed: {type(e).__name__}", file=sys.stderr)
    return list(summary.values())


def snapshot_providers(q: JobQuery, env: Any) -> list:
    """Джерела для знімка: усі доступні, крім тих, що мають жорсткі ліміти."""
    from cvmax.jobs.providers import fetchers  # лінивий імпорт

    return [p for p in fetchers(q, env) if str(getattr(p, "name", "")).casefold() not in RATE_LIMITED]


def print_summary(rows: list[dict]) -> None:
    print(f"{'program':26} {'region':26} {'fetched':>8} {'upserted':>9} {'failed':>7}")
    for r in rows:
        print(f"{r['program']:26} {r['region']:26} {r['fetched']:>8} {r['upserted']:>9} {r['failed']:>7}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    programs = args.programs or list(ROLE_QUERIES)
    unknown = [p for p in programs if p not in ROLE_QUERIES]
    if unknown:
        print("Unknown programs: " + ", ".join(unknown), file=sys.stderr)
        return 1
    db = None
    if not args.dry_run:
        url, key, token = (os.environ.get(k, "") for k in ("SUPABASE_URL", "SUPABASE_KEY", "CVMAX_DB_TOKEN"))
        if not (url and key and token):
            print("Set SUPABASE_URL, SUPABASE_KEY and CVMAX_DB_TOKEN.", file=sys.stderr)
            return 1
        db = SupabaseDB(url, key, token)
    from cvmax.jobs.search import search_jobs  # noqa: E402 (лінивий імпорт)

    summary = run_snapshot(db, lambda q: search_jobs(q, env=os.environ, providers=snapshot_providers(q, os.environ)), programs, REGIONS_TO_SNAPSHOT,
                           dry_run=args.dry_run)
    print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
