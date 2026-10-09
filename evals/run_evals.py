"""Перевірка якості аналізу на наборі CV з відомими проблемами.

Кожен кейс: CV, ціль і список того, що аналіз МУСИТЬ помітити (або НЕ чіпати).
Коли юзер знаходить пропуск, додай його сюди як новий кейс, змінюй промпт чи рубрику
і проганяй усі кейси, доки вони не проходять. Так зміни не ламають те, що вже працювало.

Запуск:  python evals/run_evals.py                 # evals/cases + evals/private
         python evals/run_evals.py шлях/до/кейсів   # інша папка
         python evals/run_evals.py --runs 3         # кожен кейс 3 рази, щоб бачити розкид
         CVMAX_PROVIDER=claude python evals/run_evals.py   # те саме на Claude (потрібен ANTHROPIC_API_KEY)
         python evals/run_evals.py --json out.json         # зберегти результат
         python evals/run_evals.py --baseline evals/baseline.json   # ворота: код 3, якщо гірше за базову лінію
         python evals/run_evals.py --update-baseline       # записати нову базову лінію
         python evals/run_evals.py --variants --baseline evals/baseline.json   # кожен активний варіант промпту окремо
         python evals/run_evals.py evals/synth --runs 1     # синтетичні кейси із закладеними вадами, є таблиця «за вадами»
         python evals/run_evals.py --raw                    # сира модель: без детермінованих перевірок і верифікатора

За замовчуванням кожен кейс іде тим самим шляхом, що й у застосунку: cvmax.analyze.analyze_full, оцінюється
його .analysis (перевірки, верифікатор правок, стабільний бал). З --raw: analyze_cv при вимкнених
config.CHECKS_ENABLED і config.VERIFY_ENABLED, щоб порівняти з тим, що каже сама модель.

Типи перевірок у expect:
  без "type"        line_contains + verdicts (+ after_must_not_contain): що аналіз каже про рядок CV;
  "length"          min_cut_words: сума (слів у before - слів у after) по правках, де after коротший;
  "mentions"        any_of: хоч одне слово є в edits[].after, edits[].section, missing_info або summary.
Для кожного кейсу додається автоматична перевірка _no_invented_numbers (вада "hallucination"): жодна правка
не вводить числа, яких немає в CV, у відомих фактах профілю чи в самій цитаті (числа в [дужках] дозволені).
Якщо хоч один expect має поле "flaw", наприкінці друкується таблиця за вадами, а в --json з'являється
"by_flaw": {код: {"passed": n, "total": m}}.

Справжні CV клади тільки в evals/private/ (ця папка не потрапляє в git).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "evals" / "baseline.json"
DEFAULT_VARIANTS_FILE = ROOT / "cvmax" / "variants" / "analysis.json"
TOLERANCE = 0.05  # допуск на шум моделі
sys.path.insert(0, str(ROOT))

from cvmax import config  # noqa: E402
from cvmax.analyze import analyze_cv, analyze_full  # noqa: E402
from cvmax.cv_input import CVFile, load_cv  # noqa: E402
from cvmax.learning.bandit import load_variants  # noqa: E402
from cvmax.llm import LLMError, make_llm  # noqa: E402
from cvmax.profile import Profile  # noqa: E402
from cvmax.verify import find_invented_numbers  # noqa: E402

# Автоматична перевірка, що додається до кожного кейсу.
HALLUCINATION_ID = "_no_invented_numbers"
HALLUCINATION_FLAW = "hallucination"
HALLUCINATION_DESCRIPTION = "No edit adds a number that is absent from the CV"


def load_case_cv(path: Path) -> CVFile:
    if path.suffix == ".txt":
        return CVFile(filename=path.name, text=path.read_text(encoding="utf-8"))
    return load_cv(path.name, path.read_bytes())


def _words(text: str) -> int:
    return len(text.split())


def check_length(expect: dict, analysis) -> tuple[bool, str]:
    """Правки скоротили CV щонайменше на min_cut_words слів (рахуємо лише правки, де after коротший за before)."""
    need = int(expect.get("min_cut_words", 100))
    cut = sum(_words(e.before) - _words(e.after) for e in analysis.edits if _words(e.after) < _words(e.before))
    return cut >= need, f"edits cut {cut} words, need at least {need}"


def check_mentions(expect: dict, analysis) -> tuple[bool, str]:
    """Хоч одне слово з any_of є в edits[].after, edits[].section, missing_info або summary."""
    needles = [w.lower() for w in expect.get("any_of", []) if w]
    if not needles:
        return False, "any_of is empty"
    parts = [analysis.summary, *analysis.missing_info]
    for e in analysis.edits:
        parts += [e.after, e.section]
    haystack = "\n".join(parts).lower()
    found = [w for w in needles if w in haystack]
    if found:
        return True, f"mentions {found[0]!r}"
    return False, f"none of {needles} in edits (after, section), missing_info or summary"


def check_no_invented_numbers(analysis, cv_text: str, background: str = "") -> tuple[bool, str]:
    """Жодна правка не вводить число, якого немає в CV, у фактах профілю або в її цитаті `before`.

    Числа в квадратних дужках ([N], [X]%) це плейсхолдери і проходять. Те саме правило, що в cvmax.verify.
    """
    for e in analysis.edits:
        invented = find_invented_numbers(e.after, f"{cv_text}\n{background}\n{e.before}")
        if invented:
            return False, f"invented numbers {invented[:5]} in edit: {e.after[:80]}"
    return True, "ok"


def check(expect: dict, analysis) -> tuple[bool, str]:
    kind = expect.get("type")
    if kind == "length":
        return check_length(expect, analysis)
    if kind == "mentions":
        return check_mentions(expect, analysis)
    if kind is not None:
        return False, f"unknown check type: {kind!r}"
    needles = [n.lower() for n in expect["line_contains"]]
    wanted = set(expect["verdicts"])

    def hits(text: str) -> bool:
        return any(n in text.lower() for n in needles)

    reviewed = [v for v in analysis.line_review if hits(v.line)]
    edited = [e for e in analysis.edits if hits(e.before)]
    forbidden = [w.lower() for w in expect.get("after_must_not_contain", [])]
    bad = [e.after for e in edited if any(w in e.after.lower() for w in forbidden)]
    if bad:
        return False, f"заборонене формулювання в правці: {bad[0][:80]}"
    if wanted == {"any"}:  # кейс перевіряє лише заборонені формулювання
        return True, "ok"
    if "keep" in wanted:
        touched = [v.verdict for v in reviewed if v.verdict != "keep"] + ["edit"] * len(edited)
        return (not touched, f"verdicts={[v.verdict for v in reviewed]} edits={len(edited)}")
    ok = any(v.verdict in wanted for v in reviewed)
    if not ok and wanted & {"cut", "shorten"}:
        ok = any(len(e.after.strip()) < len(e.before.strip()) * 0.7 for e in edited)
    return ok, f"verdicts={[v.verdict for v in reviewed]} edits={[e.after[:40] for e in edited]}"


def compare_to_baseline(result: dict, baseline: dict, tolerance: float = TOLERANCE) -> tuple[bool, str]:
    """Порівнює pass_rate прогону з базовою лінією. Повертає (чи пройшли ворота, рядок-вердикт).

    Допуск росте при малій вибірці: ворота падають, лише якщо
    pass_rate < base_rate - max(tolerance, 2 * sqrt(base_rate * (1 - base_rate) / n)),
    де n = result["total"] (кількість перевірок у прогоні).
    """
    rate = float(result.get("pass_rate", 0.0))
    if not baseline.get("total"):
        return False, f"Базова лінія порожня: спочатку запиши її (--update-baseline), set a baseline first (pass_rate {rate:.2f})."
    n = int(result["total"]) if "total" in result else None
    if n == 0:
        return False, "Нічого не оцінено (total 0), ворота закриті."
    base = float(baseline.get("pass_rate", 0.0))
    # без total у результаті (старий формат) масштабування неможливе: лишається плоский допуск
    margin = tolerance if n is None else max(tolerance, 2 * math.sqrt(max(base * (1 - base), 0.0) / n))
    ok = rate >= base - margin
    word = "ПРОЙДЕНО" if ok else "ГІРШЕ ЗА БАЗОВУ ЛІНІЮ"
    return ok, f"{word}: pass_rate {rate:.2f} проти {base:.2f} (допуск {margin:.2f}, n={n if n is not None else '?'})"


def load_flaw_map(cases: list[Path]) -> dict[tuple[str, str], str]:
    """(кейс, перевірка) -> код вади, за полями "flaw" в expect.

    Порожній, якщо жоден expect не має "flaw" (старі кейси). Інакше до нього додаємо автоматичну перевірку
    _no_invented_numbers кожного кейсу з вадою "hallucination".
    """
    flaws: dict[tuple[str, str], str] = {}
    stems: list[str] = []
    for path in cases:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        stems.append(path.stem)
        for expect in data.get("expect", []):
            if isinstance(expect, dict) and expect.get("flaw") and "id" in expect:
                flaws[(path.stem, expect["id"])] = str(expect["flaw"])
    if flaws:
        for stem in stems:
            flaws[(stem, HALLUCINATION_ID)] = HALLUCINATION_FLAW
    return flaws


def summarize_by_flaw(results: dict[tuple[str, str], list[bool]],
                      flaws: dict[tuple[str, str], str]) -> dict[str, dict[str, int]]:
    """Підсумок за вадами: {код: {"passed": n, "total": m}}. Рахуємо кожен прогін кожної перевірки."""
    by_flaw: dict[str, dict[str, int]] = {}
    for key, oks in results.items():
        code = flaws.get(key)
        if not code:
            continue
        row = by_flaw.setdefault(code, {"passed": 0, "total": 0})
        row["passed"] += sum(oks)
        row["total"] += len(oks)
    return dict(sorted(by_flaw.items()))


def print_by_flaw(by_flaw: dict[str, dict[str, int]]) -> None:
    """Таблиця за вадами: найгірші зверху."""
    print("\nBy flaw (passed/total):")
    ranked = sorted(by_flaw.items(), key=lambda kv: (kv[1]["passed"] / max(kv[1]["total"], 1), kv[0]))
    for code, row in ranked:
        rate = row["passed"] / row["total"] if row["total"] else 0.0
        print(f"  {row['passed']:>3}/{row['total']:<3} {rate:>4.0%}  {code}")


def build_result(model: str, runs: int, results: dict[tuple[str, str], list[bool]],
                 flaws: dict[tuple[str, str], str] | None = None) -> dict:
    total = sum(len(v) for v in results.values())
    passed = sum(sum(v) for v in results.values())
    result = {
        "model": model,
        "runs": runs,
        "passed": passed,
        "total": total,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "cases": {f"{c}/{k}": {"passed": sum(v), "runs": len(v)} for (c, k), v in sorted(results.items())},
    }
    by_flaw = summarize_by_flaw(results, flaws or {})
    if by_flaw:
        result["by_flaw"] = by_flaw
    return result


@contextmanager
def _raw_mode(enabled: bool):
    """На час прогону вимикає детерміновані перевірки й верифікатор правок (config), потім повертає як було."""
    if not enabled:
        yield
        return
    before = (config.CHECKS_ENABLED, config.VERIFY_ENABLED)
    config.CHECKS_ENABLED = config.VERIFY_ENABLED = False
    try:
        yield
    finally:
        config.CHECKS_ENABLED, config.VERIFY_ENABLED = before


def analyze_case(llm, profile: Profile, cv: CVFile, addendum: str = "", raw: bool = False):
    """Розбір одного кейсу. За замовчуванням як у застосунку (analyze_full), з raw це analyze_cv без допоміжних кроків."""
    if raw:
        with _raw_mode(True):
            return analyze_cv(llm, profile, cv, addendum)
    return analyze_full(llm, profile, cv, addendum).analysis


def run_cases(llm, cases: list[Path], runs: int, addendum: str = "", raw: bool = False):
    """Проганяє кейси; повертає (результати, описи перевірок)."""
    # (кейс, перевірка) -> скільки прогонів пройшло
    results: dict[tuple[str, str], list[bool]] = defaultdict(list)
    descriptions: dict[tuple[str, str], str] = {}
    for run in range(1, runs + 1):
        for path in cases:
            case = json.loads(path.read_text(encoding="utf-8"))
            cv = load_case_cv(path.parent / case["cv_file"])
            started = time.time()
            try:
                analysis = analyze_case(llm, Profile(**case["profile"]), cv, addendum, raw)
            except LLMError as e:
                print(f"[{path.stem}] ПОМИЛКА МОДЕЛІ: {e}")
                continue
            label = f"[{path.stem}]" + (f" прогін {run}" if runs > 1 else "")
            print(f"\n{label} {case.get('name', '')} ({time.time() - started:.0f} с, оцінка {analysis.overall_score})")
            checks = [(expect["id"], expect.get("description", ""), check(expect, analysis)) for expect in case["expect"]]
            # Автоматична перевірка вигаданих чисел: для кожного кейсу, окрім прописаних в expect
            checks.append((HALLUCINATION_ID, HALLUCINATION_DESCRIPTION,
                           check_no_invented_numbers(analysis, cv.text, case["profile"].get("background", ""))))
            for check_id, description, (ok, detail) in checks:
                key = (path.stem, check_id)
                results[key].append(ok)
                descriptions[key] = description
                print(f"  {'OK  ' if ok else 'FAIL'} {check_id}: {description}")
                if not ok:
                    print(f"       {detail}")
    return results, descriptions


def load_baseline(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def run_variants(llm, cases: list[Path], args) -> int:
    """Ворота для активних варіантів (текст не порожній) і запропонованих auto-* проти базової лінії."""
    if not args.baseline:
        print("--variants потребує --baseline.")
        return 1
    baseline = load_baseline(args.baseline)
    variants = [v for v in load_variants(args.variants_file.stem, args.variants_file.parent)
                if v.text.strip() and (v.active or v.id.startswith("auto-"))]
    worst = 0
    to_prune: list[str] = []
    for v in variants:
        print(f"\n=== Варіант {v.id} ===")
        results, _ = run_cases(llm, cases, args.runs, v.text, **_raw_opts(args))
        result = build_result(type(llm).__name__, args.runs, results)
        if not result["total"]:
            ok, verdict = False, f"Варіант {v.id}: нічого не оцінено, ворота закриті."
        else:
            ok, verdict = compare_to_baseline(result, baseline)
        print(f"{v.id}: {verdict}")
        if ok:
            continue
        if args.prune_failing and not v.active and v.id.startswith("auto-"):
            to_prune.append(v.id)
            print(f"Варіант {v.id} видалено з {args.variants_file}: не пройшов ворота ({verdict})")
        else:
            worst = 3
    if to_prune:
        _prune_variants(args.variants_file, to_prune)
    return worst


def _raw_opts(args) -> dict:
    """Аргумент raw передаємо лише коли він потрібен: звичайний виклик run_cases(llm, cases, runs) лишається як був."""
    return {"raw": True} if getattr(args, "raw", False) else {}


def _prune_variants(path: Path, ids: list[str]) -> None:
    """Видаляє варіанти з JSON-файлу (ключ "variants")."""
    data = json.loads(path.read_text(encoding="utf-8"))
    drop = set(ids)
    data["variants"] = [v for v in data["variants"] if v.get("id") not in drop]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Перевірка якості аналізу CV")
    parser.add_argument("dirs", nargs="*", type=Path, help="папки з кейсами")
    parser.add_argument("--runs", type=int, default=1, help="скільки разів проганяти кожен кейс")
    parser.add_argument("--json", type=Path, dest="json_path", help="записати результат у JSON")
    parser.add_argument("--baseline", type=Path, help="порівняти з базовою лінією (код 3, якщо гірше)")
    parser.add_argument("--variants", action="store_true",
                        help="прогнати кожен активний варіант з cvmax/variants/analysis.json окремо")
    parser.add_argument("--update-baseline", action="store_true", help="записати базову лінію за цим прогоном")
    parser.add_argument("--variants-file", type=Path, default=DEFAULT_VARIANTS_FILE,
                        help="JSON з варіантами промпту (за замовчуванням cvmax/variants/analysis.json)")
    parser.add_argument("--prune-failing", action="store_true",
                        help="з --variants: видалити auto-* варіант, що не пройшов ворота (не валить прогін)")
    parser.add_argument("--raw", action="store_true",
                        help="сира модель: без детермінованих перевірок і верифікатора правок (для порівняння)")
    args = parser.parse_args(argv)
    dirs = args.dirs or [ROOT / "evals" / "cases", ROOT / "evals" / "private"]
    cases = [p for d in dirs if d.exists() for p in sorted(d.glob("*.json"))]
    if not cases:
        print("Кейсів не знайдено.")
        return 1
    llm = make_llm()
    if llm is None:
        print("Потрібен GEMINI_API_KEY або ANTHROPIC_API_KEY.")
        return 1
    print(f"Модель: {type(llm).__name__}, прогонів на кейс: {args.runs}" + (", режим: raw" if args.raw else ""))
    if args.variants:
        return run_variants(llm, cases, args)
    results, descriptions = run_cases(llm, cases, args.runs, **_raw_opts(args))
    total = sum(len(v) for v in results.values())
    passed = sum(sum(v) for v in results.values())
    if args.runs > 1:
        print("\nСтабільність (скільки прогонів пройшло):")
        for (case_id, check_id), oks in sorted(results.items(), key=lambda kv: sum(kv[1]) / len(kv[1])):
            mark = "  " if all(oks) else "!!"
            print(f"  {mark} {sum(oks)}/{len(oks)}  {case_id}/{check_id}: {descriptions[(case_id, check_id)]}")
    print(f"\nРазом: {passed}/{total}")
    result = build_result(type(llm).__name__, args.runs, results, load_flaw_map(cases))
    if "by_flaw" in result:
        print_by_flaw(result["by_flaw"])
    if args.json_path:
        args.json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.update_baseline:
        if not total:  # нічого не оцінено: базову лінію не чіпаємо
            print("Нічого не оцінено, базову лінію не змінено.")
            return 3
        target = args.baseline or DEFAULT_BASELINE
        data = {k: result[k] for k in ("pass_rate", "passed", "total")}
        data["updated"] = time.strftime("%Y-%m-%d")
        data["note"] = f"Model: {result['model']}, runs: {args.runs}."
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Базову лінію записано: {target}")
        return 0
    if not total:
        print("Нічого не оцінено (усі виклики моделі впали), ворота закриті.")
        return 3
    if args.baseline and not args.update_baseline:
        gate_ok, verdict = compare_to_baseline(result, load_baseline(args.baseline))
        print(verdict)
        return 0 if gate_ok else 3  # з базовою лінією рішає ворота, а не passed == total
    return 0 if passed == total else 2


if __name__ == "__main__":
    raise SystemExit(main())
