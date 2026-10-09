"""Evals tests: check() on synthetic Analysis objects, compare_to_baseline, the analyze_full path and the --raw flag."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cvmax import config
from cvmax.demo import DEMO_CV_TEXT, FakeClient
from cvmax.schemas import Analysis, Edit, EditVerdicts, LineVerdict

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


# ---------------- Synthetic cases, the analyze_full path and the --raw flag ----------------

PROFILE = {
    "program": "economics_big_data", "status": "3rd year", "background": "", "target_role": "Data Analyst",
    "company_type": "Fintech / bank", "company_details": "", "level": "Internship", "region": "EU",
    "vacancy_text": "", "feedback_language": "English",
}


@pytest.fixture
def flags(monkeypatch):
    """The checks and the verifier are on regardless of CVMAX_CHECKS / CVMAX_VERIFY."""
    monkeypatch.setattr(config, "CHECKS_ENABLED", True)
    monkeypatch.setattr(config, "VERIFY_ENABLED", True)


def write_synth_case(folder: Path, stem: str = "demo_case") -> Path:
    """A new-format case on the demo CV: flaws, the length and mentions types. The last check fails on purpose."""
    (folder / f"{stem}.txt").write_text(DEMO_CV_TEXT, encoding="utf-8")
    case = {
        "name": "Demo", "cv_file": f"{stem}.txt", "synthetic": True, "profile": PROFILE,
        "expect": [
            {"id": "weak_opener-1", "flaw": "weak_opener", "line_contains": ["Responsible for making reports"],
             "verdicts": ["rewrite", "cut"]},
            {"id": "personal_data-1", "flaw": "personal_data", "line_contains": ["Date of birth"], "verdicts": ["cut"]},
            {"id": "over_length-1", "flaw": "over_length", "type": "length", "min_cut_words": 3},
            {"id": "missing_contact-1", "flaw": "missing_contact", "type": "mentions", "any_of": ["zzz-never-said"]},
        ],
    }
    path = folder / f"{stem}.json"
    path.write_text(json.dumps(case), encoding="utf-8")
    return path


def test_analyze_case_default_goes_through_analyze_full(monkeypatch):
    calls = []
    analysis = make_analysis()

    def fake_full(llm, profile, cv, addendum="", **kw):
        calls.append(("full", addendum))
        return SimpleNamespace(analysis=analysis)

    monkeypatch.setattr(run_evals, "analyze_full", fake_full)
    monkeypatch.setattr(run_evals, "analyze_cv", lambda *a, **k: pytest.fail("analyze_cv must not run by default"))
    assert run_evals.analyze_case("llm", None, None, "ADD") is analysis
    assert calls == [("full", "ADD")]


def test_analyze_case_raw_uses_analyze_cv_without_checks_and_verifier(monkeypatch, flags):
    seen = []
    analysis = make_analysis()

    def fake_cv(llm, profile, cv, addendum=""):
        seen.append((config.CHECKS_ENABLED, config.VERIFY_ENABLED, addendum))
        return analysis

    monkeypatch.setattr(run_evals, "analyze_cv", fake_cv)
    monkeypatch.setattr(run_evals, "analyze_full", lambda *a, **k: pytest.fail("analyze_full must not run with --raw"))
    assert run_evals.analyze_case("llm", None, None, "ADD", raw=True) is analysis
    assert seen == [(False, False, "ADD")]
    assert config.CHECKS_ENABLED is True and config.VERIFY_ENABLED is True  # the flags are restored


def test_raw_mode_restores_flags_after_an_error(flags):
    with pytest.raises(RuntimeError):
        with run_evals._raw_mode(True):
            assert config.CHECKS_ENABLED is False
            raise RuntimeError("boom")
    assert config.CHECKS_ENABLED is True and config.VERIFY_ENABLED is True


def test_default_run_uses_checks_and_verifier_and_raw_run_does_not(tmp_path, flags):
    path = write_synth_case(tmp_path)
    full, raw = FakeClient(), FakeClient()
    run_evals.run_cases(full, [path], 1)
    run_evals.run_cases(raw, [path], 1, raw=True)
    assert [c["output_format"] for c in full.calls] == [Analysis, EditVerdicts]
    assert "<checks>" in full.calls[0]["messages"][0]["content"][-1]["text"]
    assert [c["output_format"] for c in raw.calls] == [Analysis]
    assert "<checks>" not in raw.calls[0]["messages"][0]["content"][-1]["text"]


def test_run_cases_adds_the_hallucination_check_to_every_case(tmp_path, flags):
    path = write_synth_case(tmp_path)
    results, descriptions = run_evals.run_cases(FakeClient(), [path], 1)
    assert results[("demo_case", "weak_opener-1")] == [True]
    assert results[("demo_case", "personal_data-1")] == [True]
    assert results[("demo_case", "over_length-1")] == [True]
    assert results[("demo_case", "missing_contact-1")] == [False]
    assert results[("demo_case", run_evals.HALLUCINATION_ID)] == [True]
    assert descriptions[("demo_case", run_evals.HALLUCINATION_ID)]


def test_main_prints_and_saves_by_flaw(tmp_path, monkeypatch, capsys, flags):
    write_synth_case(tmp_path)
    monkeypatch.setattr(run_evals, "make_llm", lambda: FakeClient())
    out = tmp_path / "result.json"
    code = run_evals.main([str(tmp_path), "--json", str(out)])
    assert code == 2  # missing_contact fails on purpose
    by_flaw = json.loads(out.read_text(encoding="utf-8"))["by_flaw"]
    assert by_flaw == {
        "hallucination": {"passed": 1, "total": 1},
        "missing_contact": {"passed": 0, "total": 1},
        "over_length": {"passed": 1, "total": 1},
        "personal_data": {"passed": 1, "total": 1},
        "weak_opener": {"passed": 1, "total": 1},
    }
    printed = capsys.readouterr().out
    assert "By flaw" in printed and "missing_contact" in printed


def test_old_case_format_runs_unchanged_and_has_no_by_flaw(tmp_path, monkeypatch, capsys, flags):
    monkeypatch.setattr(run_evals, "make_llm", lambda: FakeClient())
    out = tmp_path / "result.json"
    cases = ROOT / "evals" / "cases"
    run_evals.main([str(cases), "--json", str(out)])
    data = json.loads(out.read_text(encoding="utf-8"))
    n_cases = len(list(cases.glob("*.json")))
    n_expect = sum(len(json.loads(p.read_text(encoding="utf-8"))["expect"]) for p in cases.glob("*.json"))
    assert "by_flaw" not in data and data["total"] == n_expect + n_cases  # one automatic check per case
    assert "By flaw" not in capsys.readouterr().out


def test_main_raw_flag_runs_analyze_cv(tmp_path, monkeypatch, capsys, flags):
    write_synth_case(tmp_path)
    seen = []

    def fake_cv(llm, profile, cv, addendum=""):
        seen.append((config.CHECKS_ENABLED, config.VERIFY_ENABLED))
        return make_analysis()

    monkeypatch.setattr(run_evals, "make_llm", lambda: object())
    monkeypatch.setattr(run_evals, "analyze_cv", fake_cv)
    monkeypatch.setattr(run_evals, "analyze_full", lambda *a, **k: pytest.fail("analyze_full must not run with --raw"))
    run_evals.main([str(tmp_path), "--raw"])
    assert seen == [(False, False)] and config.CHECKS_ENABLED is True
    assert "raw" in capsys.readouterr().out


def test_raw_flag_reaches_run_cases_in_variants_mode(tmp_path, monkeypatch):
    seen = []
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"pass_rate": 0.5, "total": 10}), encoding="utf-8")
    vfile = tmp_path / "analysis.json"
    vfile.write_text(json.dumps({"task": "analysis", "variants": [{"id": "act", "active": True, "text": "A"}]}),
                     encoding="utf-8")
    (tmp_path / "case.json").write_text("{}", encoding="utf-8")

    def fake_run_cases(llm, cases, runs, addendum="", raw=False):
        seen.append(raw)
        return {("c", "k"): [True] * 10}, {}

    monkeypatch.setattr(run_evals, "make_llm", lambda: object())
    monkeypatch.setattr(run_evals, "run_cases", fake_run_cases)
    run_evals.main([str(tmp_path), "--variants", "--raw", "--baseline", str(baseline), "--variants-file", str(vfile)])
    run_evals.main([str(tmp_path), "--variants", "--baseline", str(baseline), "--variants-file", str(vfile)])
    assert seen == [True, False]
