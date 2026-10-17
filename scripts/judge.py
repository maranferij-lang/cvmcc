"""Weekly independent judge of opted-in anonymised samples.

Run:  python scripts/judge.py --days 7 --limit 20
      python scripts/judge.py --days 90 --limit 0 --export-cases evals/private/judged   # export only, judge nothing
Required variables: SUPABASE_URL, SUPABASE_KEY, CVMAX_LEARN_TOKEN and, unless --limit 0, a model key.
The report docs/judge/YYYY-MM-DD.md has counts only: no CV text, lines or reasons.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cvmax.db import SupabaseDB  # noqa: E402
from cvmax.judge import (  # noqa: E402
    ExportRefused, check_export_dir, export_cases, judge_sample, judgement_record, render_report, today,
)
from cvmax.llm import LLMError, make_llm  # noqa: E402

log = logging.getLogger(__name__)
EXPORT_MAX = 200


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--limit", type=int, default=20, help="samples to judge; 0 = judge nothing (export only)")
    ap.add_argument("--export-cases", type=Path, default=None, metavar="DIR",
                    help="write judged samples as eval cases; DIR must be under evals/private/")
    ap.add_argument("--report-dir", type=Path, default=ROOT / "docs" / "judge")
    ap.add_argument("--today", default=None, help="YYYY-MM-DD, default today")
    return ap.parse_args(argv)


def run(db, llm, args: argparse.Namespace, day: str, root: Path = ROOT) -> dict:
    """One pass. Returns counts. A failed judge call skips that sample; nothing here raises on model errors."""
    result = {"judged": 0, "failed": 0, "exported": 0, "report": None}
    try:  # retention runs on every pass, also export-only (--limit 0)
        db.purge_training_samples()
    except Exception as exc:  # noqa: BLE001 - purge is housekeeping; type only, a message could hold CV text
        log.warning("purge failed (%s)", type(exc).__name__)
    if args.limit > 0:
        samples = db.training_samples_for_judge(days=args.days, limit=args.limit)
        done: list[tuple[dict, dict]] = []
        for sample in samples:
            try:
                record = judgement_record(judge_sample(llm, sample), day)
                db.save_judgement(sample["id"], record)
            except LLMError:
                result["failed"] += 1
                continue
            except Exception as exc:  # noqa: BLE001 - one bad sample must not stop the run
                log.warning("judging a sample failed (%s)", type(exc).__name__)
                result["failed"] += 1
                continue
            done.append((sample, record))
        result["judged"] = len(done)
        text = render_report(done, result["failed"], day, args.days)
        path = args.report_dir / f"{day}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        result["report"] = path
    if args.export_cases is not None:
        written = export_cases(db.judged_samples(days=args.days, limit=EXPORT_MAX), args.export_cases, root)
        result["exported"] = len(written)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.export_cases is not None:
        try:
            check_export_dir(args.export_cases, ROOT)  # refuse before any network call
        except ExportRefused as e:
            print(str(e), file=sys.stderr)
            return 2
    url, key, token = (os.environ.get(k, "") for k in ("SUPABASE_URL", "SUPABASE_KEY", "CVMAX_LEARN_TOKEN"))
    if not (url and key and token):
        print("Set SUPABASE_URL, SUPABASE_KEY and CVMAX_LEARN_TOKEN.", file=sys.stderr)
        return 1
    llm = None
    if args.limit > 0:
        try:
            llm = make_llm()
        except LLMError as e:
            print(str(e), file=sys.stderr)
            return 1
        if llm is None:
            print("No model key found (ANTHROPIC_API_KEY or GEMINI_API_KEY).", file=sys.stderr)
            return 1
    result = run(SupabaseDB(url, key, token), llm, args, args.today or today())
    print(f"judged {result['judged']}, failed {result['failed']}, exported {result['exported']}")
    if result["report"]:
        print(f"wrote {result['report']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
