"""Evals tests: check() on synthetic Analysis objects and compare_to_baseline."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from cvmax.schemas import Analysis, Edit, LineVerdict

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("run_evals", ROOT / "evals" / "run_evals.py")
run_evals = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_evals)


def make_analysis(lines=(), edits=()) -> Analysis:
    return Analysis(
        overall_score=50, summary="", target_assumptions=[], scores=[], strengths=[],
        line_review=[LineVerdict(line=line, verdict=v, reason="") for line, v in lines],
        edits=[Edit(section="x", before=b, after=a, reason="", priority="high") for b, a in edits],
        gaps=[], missing_info=[],
    )


def expectation(case: str, check_id: str) -> dict:
    data = json.loads((ROOT / "evals" / "cases" / f"{case}.json").read_text(encoding="utf-8"))
    return next(e for e in data["expect"] if e["id"] == check_id)


def test_cut_expectation_passes_on_cut_verdict():
    exp = expectation("backend_student", "date-of-birth")
    ok, _ = run_evals.check(exp, make_analysis(lines=[("Date of birth: 14 March 2005", "cut")]))
    assert ok


def test_cut_expectation_fails_on_keep_verdict():
    exp = expectation("backend_student", "date-of-birth")
    ok, _ = run_evals.check(exp, make_analysis(lines=[("Date of birth: 14 March 2005", "keep")]))
    assert not ok


def test_forbidden_word_in_after_fails():
    exp = expectation("backend_student", "no-invented-tools")
    analysis = make_analysis(edits=[("Responsible for developing the backend", "Deployed it on Kubernetes")])
    ok, detail = run_evals.check(exp, analysis)
    assert not ok and "Kubernetes" in detail


def test_forbidden_word_absent_passes():
    exp = expectation("consulting_student", "no-invented-analysis")
    analysis = make_analysis(edits=[("Organised a career day for 300", "Organised a career day for 300 students")])
    assert run_evals.check(exp, analysis)[0]


def test_analys_is_forbidden_case_insensitive():
    exp = expectation("consulting_student", "no-invented-analysis")
    analysis = make_analysis(edits=[("Organised a career day", "Analysed industries to organise a career day")])
    assert not run_evals.check(exp, analysis)[0]


def test_keep_expectation_fails_when_edited():
    exp = expectation("law_student", "keep-moot-court")
    analysis = make_analysis(edits=[("reached the semi-final out of 24 teams", "Reached semi-final")])
    assert not run_evals.check(exp, analysis)[0]


def test_keep_expectation_passes_when_untouched():
    exp = expectation("law_student", "keep-moot-court")
    assert run_evals.check(exp, make_analysis(lines=[("Argued ... semi-final", "keep")]))[0]


def test_new_cases_are_well_formed():
    for name in ("backend_student", "consulting_student", "law_student"):
        case = json.loads((ROOT / "evals" / "cases" / f"{name}.json").read_text(encoding="utf-8"))
        assert (ROOT / "evals" / "cases" / case["cv_file"]).exists()
        assert 4 <= len(case["expect"]) <= 6
        text = (ROOT / "evals" / "cases" / case["cv_file"]).read_text(encoding="utf-8")
        for exp in case["expect"]:
            assert any(n.lower() in text.lower() for n in exp["line_contains"]), exp["id"]


def test_compare_equal():
    ok, msg = run_evals.compare_to_baseline({"pass_rate": 0.8}, {"pass_rate": 0.8, "total": 10})
    assert ok and msg


def test_compare_within_tolerance():
    assert run_evals.compare_to_baseline({"pass_rate": 0.76}, {"pass_rate": 0.8, "total": 10})[0]


def test_compare_clearly_lower():
    ok, msg = run_evals.compare_to_baseline({"pass_rate": 0.6}, {"pass_rate": 0.8, "total": 10})
    assert not ok and "0.60" in msg


def test_compare_empty_baseline_fails_closed():
    ok, msg = run_evals.compare_to_baseline({"pass_rate": 0.1}, {"pass_rate": 0.0, "total": 0})
    assert not ok and "baseline" in msg
    assert not run_evals.compare_to_baseline({"pass_rate": 0.1}, {})[0]


def test_build_result_shape():
    res = run_evals.build_result("M", 2, {("a", "b"): [True, False]})
    assert res["pass_rate"] == 0.5 and res["cases"]["a/b"] == {"passed": 1, "runs": 2}


def test_compare_tolerance_scales_with_sample_size():
    base = {"pass_rate": 0.9, "total": 36}
    assert run_evals.compare_to_baseline({"pass_rate": 0.82, "total": 36}, base)[0]
    assert not run_evals.compare_to_baseline({"pass_rate": 0.75, "total": 36}, base)[0]
    assert not run_evals.compare_to_baseline({"pass_rate": 0.9, "total": 0}, base)[0]


def _setup_variants(tmp_path, monkeypatch, calls):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"pass_rate": 0.5, "total": 10}), encoding="utf-8")
    vfile = tmp_path / "analysis.json"
    vfile.write_text(json.dumps({"task": "analysis", "variants": [
        {"id": "act", "active": True, "text": "A"},
        {"id": "auto-x", "active": False, "text": "B"},
    ]}), encoding="utf-8")
    case = tmp_path / "case.json"
    case.write_text("{}", encoding="utf-8")

    def fake_run_cases(llm, cases, runs, addendum=""):
        calls.append(addendum)
        ok = addendum == "A"
        return {("c", "k"): [ok] * 10}, {}

    monkeypatch.setattr(run_evals, "make_llm", lambda: object())
    monkeypatch.setattr(run_evals, "run_cases", fake_run_cases)
    return baseline, vfile, case


def test_variants_gate_fails_without_prune(tmp_path, monkeypatch):
    calls: list[str] = []
    baseline, vfile, case = _setup_variants(tmp_path, monkeypatch, calls)
    code = run_evals.main([str(tmp_path), "--variants", "--baseline", str(baseline),
                           "--variants-file", str(vfile), "--runs", "1"])
    assert code == 3 and calls == ["A", "B"]


def test_variants_prune_failing_removes_auto_variant(tmp_path, monkeypatch):
    calls: list[str] = []
    baseline, vfile, case = _setup_variants(tmp_path, monkeypatch, calls)
    code = run_evals.main([str(tmp_path), "--variants", "--prune-failing", "--baseline", str(baseline),
                           "--variants-file", str(vfile), "--runs", "1"])
    ids = [v["id"] for v in json.loads(vfile.read_text(encoding="utf-8"))["variants"]]
    assert code == 0 and ids == ["act"]


def test_update_baseline_returns_zero_and_writes(tmp_path, monkeypatch):
    calls: list[str] = []
    _, _, case = _setup_variants(tmp_path, monkeypatch, calls)
    target = tmp_path / "new_baseline.json"
    code = run_evals.main([str(tmp_path), "--update-baseline", "--baseline", str(target)])
    assert code == 0 and json.loads(target.read_text(encoding="utf-8"))["total"] == 10
