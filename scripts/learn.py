"""Тижнева рефлексія: відгуки студентів -> rubrics/learned/<сфера>.md, звіт і нові варіанти промпту.

Запуск:  python scripts/learn.py --days 30
         python scripts/learn.py --dry-run          # лише показати, нічого не писати
Потрібні змінні: SUPABASE_URL, SUPABASE_KEY, CVMAX_DB_TOKEN і ключ моделі (GEMINI_API_KEY тощо).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cvmax.db import SupabaseDB  # noqa: E402
from cvmax.learning.reflect import (  # noqa: E402
    LessonsReport, program_stats, reflect, render_learned, revert_if_worse, sample_edits, summarise,
)
from cvmax.learning import bandit  # noqa: E402
from cvmax.learning.version import lessons_version  # noqa: E402
from cvmax.llm import LLMError, make_llm  # noqa: E402
from cvmax.profile import PROGRAMS  # noqa: E402

MAX_VARIANTS = 5    # рахуємо всі активні та всі auto-* (в очікуванні людини) варіанти
log = logging.getLogger(__name__)
MIN_USERS = 10      # скільки різних користувачів потрібно сфері, щоб писати уроки для всіх
MIN_ANALYSES = 20   # і скільки різних аналізів


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--min-signals", type=int, default=20)
    ap.add_argument("--min-users", type=int, default=MIN_USERS)
    ap.add_argument("--min-analyses", type=int, default=MIN_ANALYSES)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--learned-dir", type=Path, default=ROOT / "cvmax" / "rubrics" / "learned")
    ap.add_argument("--report-dir", type=Path, default=ROOT / "docs" / "learning")
    ap.add_argument("--variants", type=Path, default=ROOT / "cvmax" / "variants" / "analysis.json")
    ap.add_argument("--today", default=None, help="YYYY-MM-DD, за замовчуванням сьогодні")
    return ap.parse_args(argv)


def append_variant(variants_path: Path, program: str, text: str, date_str: str,
                   off: frozenset[str] | set[str] = frozenset(), notes: list[str] | None = None) -> str | None:
    """Додає варіант у JSON (неактивний: вмикає лише людина). Повертає id або None.

    Ліміт MAX_VARIANTS рахує усі активні варіанти й усі auto-*. Якщо для цієї сфери вже є
    неактивний auto-* варіант, його текст і id замінюються (нова дата), а не додається ще один.
    Коли ліміт досягнуто і такого варіанта немає, нічого не додаємо й пишемо примітку в notes.
    Параметр off лишено для сумісності: рішення бандита ліміт не змінюють.
    """
    data = json.loads(variants_path.read_text(encoding="utf-8"))
    items = data.get("variants", [])
    vid = f"auto-{date_str.replace('-', '')}-{program}"
    pending = next((v for v in items if not v.get("active") and str(v.get("id", "")).startswith("auto-")
                    and program in (v.get("programs") or [])), None)
    if pending is not None:
        if any(v is not pending and v.get("id") == vid for v in items):
            return None
        pending["id"], pending["text"] = vid, text
    else:
        used = sum(1 for v in items if v.get("active") or str(v.get("id", "")).startswith("auto-"))
        if used >= MAX_VARIANTS or any(v.get("id") == vid for v in items):
            if notes is not None:
                notes.append(f"variant not saved: limit of {MAX_VARIANTS} variants reached")
            return None
        items.append({"id": vid, "active": False, "programs": [program], "text": text})
    data["variants"] = items
    variants_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return vid


def variant_totals(stats: list[dict]) -> dict[str, tuple[int, int]]:
    """Справжні суми успіхів/невдач по варіанту за всіма рядками (без підміни рядком програми)."""
    totals: dict[str, list[int]] = {}
    for row in stats:
        t = totals.setdefault(str(row["variant"]), [0, 0])
        t[0] += int(row.get("successes", 0))
        t[1] += int(row.get("failures", 0))
    return {k: (v[0], v[1]) for k, v in totals.items()}


def disabled_active(stats: list[dict], variants_dir: Path) -> set[str]:
    """Варіанти, які бандит зараз відключив; порівнюємо лише активні, останній активний лишається."""
    active = {v.id for v in bandit.load_variants("analysis", variants_dir) if v.active}
    totals = variant_totals(stats)
    off = bandit.disabled_variants({k: v for k, v in totals.items() if k in active})
    return set() if len(active - off) < 1 else off


def has_breadth(export: dict, program: str, args: argparse.Namespace) -> bool:
    """Уроки йдуть усім студентам сфери, тож їх мають давати багато різних людей і аналізів."""
    breadth = export.get("breadth")  # експорту без ключа breadth не довіряємо: порожньо
    b = (breadth.get(program) if isinstance(breadth, dict) else None) or {}
    if not isinstance(b, dict):
        b = {}
    try:
        return int(b.get("users", 0)) >= args.min_users and int(b.get("analyses", 0)) >= args.min_analyses
    except (TypeError, ValueError):
        return False


def _table(headers: list[str], rows: list[list]) -> list[str]:
    if not rows:
        return ["_no data_", ""]

    def esc(cell) -> str:  # "|" розбиває рядок markdown-таблиці
        return str(cell).replace("|", "\\|")

    out = ["| " + " | ".join(esc(h) for h in headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]
    return out + [""]


def render_report(stats: dict, notes: dict[str, str], reverted: str, date_str: str, days: int) -> str:
    """Markdown-звіт тижня: таблиці статистики й підсумки по сферах."""
    lines = [f"# Weekly lessons {date_str}", "", f"Window: last {days} days.", ""]
    if reverted:
        lines += [f"**Reverted:** {reverted}", ""]
    lines += ["## Signals per program", ""]
    lines += _table(["program", "signals"], sorted(stats["signals"].items()))
    for title, key in (("Accept rate by section", "accept_by_section"),
                       ("Accept rate by priority", "accept_by_priority")):
        rows = [[p, k, f"{c['accepted']}/{c['total']}", f"{c['rate']:.0%}"]
                for p, d in sorted(stats[key].items()) for k, c in sorted(d.items())]
        lines += [f"## {title}", ""] + _table(["program", "key", "accepted", "rate"], rows)
    for title, key in (("Thumbs up by variant", "thumbs_by_variant"),
                       ("Thumbs up by knowledge version", "thumbs_by_version")):
        rows = [[k, f"{c['up']}/{c['total']}", f"{c['rate']:.0%}"] for k, c in sorted(stats[key].items())]
        lines += [f"## {title}", ""] + _table(["id", "up", "rate"], rows)
    r, g, o = stats["rescan"], stats["grill"], stats["outcomes"]
    lines += ["## Other", "",
              f"- Rescan score change: mean {r['mean']} over {r['n']} rescans",
              f"- Grill me: {g['answered']} answered, {g['skipped']} skipped",
              f"- Interview outcome: {o['yes']} yes, {o['no']} no, {o['not_yet']} not yet", ""]
    if notes:
        lines += ["## Per-program summaries", ""]
        lines += [f"### {p}\n\n{text}\n" for p, text in notes.items()]
    return "\n".join(lines).rstrip() + "\n"


def run(db, llm, args: argparse.Namespace, date_str: str) -> dict:
    """Один прохід. Повертає {'stats','written','reverted','report'}; у dry-run нічого не пише."""
    export = db.learning_export(args.days)
    stats = summarise(export)
    written: list[Path] = []
    notes: dict[str, str] = {}
    reverted = ""
    off: set[str] = set()
    if not args.dry_run:
        try:
            off = disabled_active(db.variant_stats("analysis"), args.variants.parent)
        except Exception:  # noqa: BLE001 - статистика варіантів не критична для тижневого прогону
            off = set()

    def write(path: Path, text: str) -> None:
        if not args.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            written.append(path)

    worse, reason = revert_if_worse(stats, lessons_version(args.learned_dir.parents[1]))
    if worse:
        reverted = reason
        for f in sorted(args.learned_dir.glob("*.md")):
            write(f, f"<!-- reverted on {date_str}: {reason} -->\n")
    else:  # після відкату нові уроки не пишемо: спершу треба нові дані
        for program in PROGRAMS:
            n = stats["signals"].get(program, 0)
            if n < args.min_signals or not has_breadth(export, program, args):
                continue
            accepted, rejected = sample_edits(export, program)
            try:
                report: LessonsReport = reflect(llm, program, program_stats(stats, program), accepted, rejected)
            except LLMError as e:
                log.warning("reflection for %s failed: %s", program, e)
                notes[program] = f"skipped: reflection failed ({str(e)[:200]})"
                continue
            write(args.learned_dir / f"{program}.md", render_learned(program, report, n, date_str))
            notes[program] = report.summary
            if report.proposed_variant and not args.dry_run:
                extra: list[str] = []
                append_variant(args.variants, program, report.proposed_variant, date_str, off, extra)
                if extra:
                    notes[program] += "\n\n" + "; ".join(extra)
            elif report.proposed_variant:
                notes[program] += f"\n\nProposed variant (dry-run, not saved): {report.proposed_variant}"
    text = render_report(stats, notes, reverted, date_str, args.days)
    write(args.report_dir / f"{date_str}.md", text)
    return {"stats": stats, "written": written, "reverted": reverted, "report": text}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    date_str = args.today or date.today().isoformat()
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
    result = run(SupabaseDB(url, key, token), llm, args, date_str)
    if args.dry_run:
        print(result["report"])
    else:
        for p in result["written"]:
            print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
