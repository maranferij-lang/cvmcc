"""Перевірка якості аналізу на наборі CV з відомими проблемами.

Кожен кейс: CV, ціль і список того, що аналіз МУСИТЬ помітити (або НЕ чіпати).
Коли юзер знаходить пропуск, додай його сюди як новий кейс, змінюй промпт чи рубрику
і проганяй усі кейси, доки вони не проходять. Так зміни не ламають те, що вже працювало.

Запуск:  python evals/run_evals.py                 # evals/cases + evals/private
         python evals/run_evals.py шлях/до/кейсів   # інша папка

Справжні CV клади тільки в evals/private/ (ця папка не потрапляє в git).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cvmax.analyze import analyze_cv  # noqa: E402
from cvmax.cv_input import CVFile, load_cv  # noqa: E402
from cvmax.llm import LLMError, make_llm  # noqa: E402
from cvmax.profile import Profile  # noqa: E402


def load_case_cv(path: Path) -> CVFile:
    if path.suffix == ".txt":
        return CVFile(filename=path.name, text=path.read_text(encoding="utf-8"))
    return load_cv(path.name, path.read_bytes())


def check(expect: dict, analysis) -> tuple[bool, str]:
    needles = [n.lower() for n in expect["line_contains"]]
    wanted = set(expect["verdicts"])

    def hits(text: str) -> bool:
        return any(n in text.lower() for n in needles)

    reviewed = [v for v in analysis.line_review if hits(v.line)]
    edited = [e for e in analysis.edits if hits(e.before)]
    if "keep" in wanted:
        touched = [v.verdict for v in reviewed if v.verdict != "keep"] + ["edit"] * len(edited)
        return (not touched, f"verdicts={[v.verdict for v in reviewed]} edits={len(edited)}")
    ok = any(v.verdict in wanted for v in reviewed)
    if not ok and wanted & {"cut", "shorten"}:
        ok = any(len(e.after.strip()) < len(e.before.strip()) * 0.7 for e in edited)
    return ok, f"verdicts={[v.verdict for v in reviewed]} edits={[e.after[:40] for e in edited]}"


def main() -> int:
    dirs = [Path(a) for a in sys.argv[1:]] or [ROOT / "evals" / "cases", ROOT / "evals" / "private"]
    cases = [p for d in dirs if d.exists() for p in sorted(d.glob("*.json"))]
    if not cases:
        print("Кейсів не знайдено.")
        return 1
    llm = make_llm()
    if llm is None:
        print("Потрібен GEMINI_API_KEY або ANTHROPIC_API_KEY.")
        return 1
    total = passed = 0
    for path in cases:
        case = json.loads(path.read_text(encoding="utf-8"))
        cv = load_case_cv(path.parent / case["cv_file"])
        started = time.time()
        try:
            analysis = analyze_cv(llm, Profile(**case["profile"]), cv)
        except LLMError as e:
            print(f"[{path.stem}] ПОМИЛКА МОДЕЛІ: {e}")
            continue
        print(f"\n[{path.stem}] {case.get('name', '')} ({time.time() - started:.0f} с, оцінка {analysis.overall_score})")
        for expect in case["expect"]:
            ok, detail = check(expect, analysis)
            total += 1
            passed += ok
            print(f"  {'OK  ' if ok else 'FAIL'} {expect['id']}: {expect.get('description', '')}")
            if not ok:
                print(f"       {detail}")
    print(f"\nРазом: {passed}/{total}")
    return 0 if passed == total else 2


if __name__ == "__main__":
    raise SystemExit(main())
