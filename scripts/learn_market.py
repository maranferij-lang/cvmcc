"""Skills market: job snapshot -> cvmax_skill_demand and cvmax/rubrics/market/<direction>.md.

Run:  python scripts/learn_market.py --days 30
      python scripts/learn_market.py --dry-run
Required variables: SUPABASE_URL, SUPABASE_KEY, CVMAX_DB_TOKEN and a model key (GEMINI_API_KEY etc.).
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cvmax.db import SupabaseDB  # noqa: E402
from cvmax.learning.market import (  # noqa: E402
    REGIONS_TO_SNAPSHOT, ROLE_QUERIES, aggregate, extract_skills, render_market,
)
from cvmax.llm import LLMError, make_llm  # noqa: E402

MAX_POSTINGS = 300
MIN_POSTINGS = 20
MIN_COVERAGE = 0.8  # the share of postings that passed skill extraction; below it we skip the region


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--today", default=None, help="YYYY-MM-DD, за замовчуванням сьогодні")
    ap.add_argument("--market-dir", type=Path, default=ROOT / "cvmax" / "rubrics" / "market")
    ap.add_argument("--programs", nargs="*", default=None)
    return ap.parse_args(argv)


def collect_postings(db: Any, program: str, region: str, days: int) -> list[dict]:
    """Postings across all queries of a direction, without duplicates by link, no more than MAX_POSTINGS."""
    seen: set[str] = set()
    out: list[dict] = []
    for query in ROLE_QUERIES.get(program, []):
        for r in db.recent_vacancies(role_family=query, region=region, days=days, limit=MAX_POSTINGS):
            key = r.get("url") or ""
            if not key or key in seen:
                continue
            seen.add(key)
            out.append({**r, "id": str(len(out) + 1)})  # our own ids: in MemoryDB they are positional and match
    return out[:MAX_POSTINGS]


def run_market(db: Any, llm: Any, programs: list[str], regions: list[str], days: int, today: str,
               market_dir: Path, dry_run: bool = False) -> dict:
    """Returns {'by_program': {program: {region: (total, rows)}}, 'written': [Path]}."""
    by_program: dict[str, dict] = {}
    written: list[Path] = []
    for program in programs:
        by_region: dict[str, tuple[int, list[dict]]] = {}
        for region in regions:
            postings = collect_postings(db, program, region, days)
            if not postings:
                continue
            skills, covered = extract_skills(llm, postings)
            coverage = len(covered) / len(postings)
            rows = aggregate(skills, len(covered))
            if coverage < MIN_COVERAGE or not rows:
                print(f"{program} / {region}: skipped (coverage {coverage:.0%}, {len(rows)} skills); "
                      "previous data kept")
                continue
            by_region[region] = (len(covered), rows)
            if not dry_run:
                db.save_skill_demand([{"program": program, "region": region, "skill": r["skill"],
                                       "postings": r["postings"], "total_postings": r["total_postings"],
                                       "window_days": days} for r in rows])
        by_program[program] = by_region
        # Skipped regions (low coverage) stay in the rubric with the previous data from the DB
        merged = dict(by_region)
        if not dry_run:
            for region in regions:
                if region in merged:
                    continue
                prev = db.skill_demand(program, region)
                if prev:
                    total_prev = max(int(r.get("total_postings") or 0) for r in prev)
                    if total_prev:
                        merged[region] = (total_prev, [
                            {**r, "share": r["postings"] / total_prev} for r in prev])
        if not any(total >= MIN_POSTINGS for total, _ in by_region.values()):
            continue
        if not dry_run:
            market_dir.mkdir(parents=True, exist_ok=True)
            path = market_dir / f"{program}.md"
            path.write_text(render_market(program, merged, days, today), encoding="utf-8")
            written.append(path)
    return {"by_program": by_program, "written": written}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    today = args.today or date.today().isoformat()
    url, key, token = (os.environ.get(k, "") for k in ("SUPABASE_URL", "SUPABASE_KEY", "CVMAX_DB_TOKEN"))
    if not (url and key and token):
        print("Set SUPABASE_URL, SUPABASE_KEY and CVMAX_DB_TOKEN.", file=sys.stderr)
        return 1
    try:
        llm = make_llm()
    except LLMError as e:
        print(str(e), file=sys.stderr)
        return 1
    if llm is None:
        print("No model key found (GEMINI_API_KEY or ANTHROPIC_API_KEY).", file=sys.stderr)
        return 1
    programs = args.programs or list(ROLE_QUERIES)
    result = run_market(SupabaseDB(url, key, token), llm, programs, REGIONS_TO_SNAPSHOT, args.days, today,
                        args.market_dir, args.dry_run)
    for program, by_region in result["by_program"].items():
        for region, (total, rows) in by_region.items():
            top = ", ".join(f"{r['skill']} {round(r['share'] * 100)}%" for r in rows[:5])
            print(f"{program} / {region}: {total} postings. {top}")
    for p in result["written"]:
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
