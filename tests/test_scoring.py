"""Stable score: criterion matching, weights, formula, penalty, knowledge version."""
import json
import shutil
import sys
import types
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

import cvmax
from cvmax import config, scoring
from cvmax.demo import demo_analysis
from cvmax.learning import version
from cvmax.profile import PROGRAMS
from cvmax.schemas import CriterionScore
from cvmax.scoring import MIN_MATCHED, ScoreDetail, compute_score, load_weights, match_criterion

DEFAULT_KEYS = [
    "Target fit",
    "Impact bullets",
    "Evidence and numbers",
    "Structure and scannability",
    "Length and density",
    "ATS-friendliness",
    "Language quality",
]
RUBRICS = Path(cvmax.__file__).parent / "rubrics"


@pytest.fixture(autouse=True)
def _config_values(monkeypatch):
    """Pin the values from the plan so the tests do not depend on whether config already has them."""
    monkeypatch.setattr(config, "SCORE_MODEL_WEIGHT", 0.3, raising=False)
    monkeypatch.setattr(config, "PENALTY_CAP", 15, raising=False)


@pytest.fixture
def stub_checks(monkeypatch):
    """Replaces cvmax.checks with a stub without penalty: the calculation runs locally."""
    stub = types.ModuleType("cvmax.checks")
    monkeypatch.setitem(sys.modules, "cvmax.checks", stub)
    monkeypatch.setattr(cvmax, "checks", stub, raising=False)
    return stub


def _report(*severities):
    return SimpleNamespace(findings=[SimpleNamespace(severity=s) for s in severities])


def _analysis(grades, overall=58):
    """demo_analysis with different criterion scores: grades = a list of (name, score) or a dict."""
    pairs = list(grades.items()) if isinstance(grades, dict) else list(grades)
    scores = [CriterionScore(criterion=n, score=s, comment="c") for n, s in pairs]
    return demo_analysis().model_copy(update={"scores": scores, "overall_score": overall})


def _uniform(grade, overall):
    return _analysis({k: grade for k in DEFAULT_KEYS}, overall)


# --- matching a criterion name ---


@pytest.mark.parametrize("key", DEFAULT_KEYS)
def test_match_exact(key):
    weights = load_weights("other")
    assert match_criterion(key, weights) == key
    assert match_criterion(key.upper(), weights) == key
    assert match_criterion("  " + key.lower() + "  ", weights) == key


def test_match_ignores_punctuation_in_names():
    weights = load_weights("other")
    assert match_criterion("ATS friendliness", weights) == "ATS-friendliness"
    assert match_criterion("ats-friendliness", weights) == "ATS-friendliness"
    assert match_criterion("1. Target fit:", weights) == "Target fit"


def test_match_extra_words():
    weights = load_weights("other")
    assert match_criterion("Target fit for the role", weights) == "Target fit"
    assert match_criterion("Impact bullets (achievements, not duties)", weights) == "Impact bullets"
    assert match_criterion("Overall length and density of the CV", weights) == "Length and density"
    assert match_criterion("Evidence and numbers in bullets", weights) == "Evidence and numbers"


def test_match_word_order_variants():
    weights = load_weights("other")
    assert match_criterion("Fit target", weights) == "Target fit"
    assert match_criterion("Numbers and evidence", weights) == "Evidence and numbers"
    assert match_criterion("Scannability and structure", weights) == "Structure and scannability"
    assert match_criterion("Quality of language", weights) == "Language quality"
    assert match_criterion("Density and length", weights) == "Length and density"


def test_match_by_first_word():
    weights = load_weights("other")
    assert match_criterion("Structure", weights) == "Structure and scannability"
    assert match_criterion("ATS compatibility", weights) == "ATS-friendliness"
    assert match_criterion("Impact", weights) == "Impact bullets"


def test_match_unknown_names():
    weights = load_weights("other")
    for name in ("Creativity", "Overall impression", "Fit", "Quality", "", "   ", "123", "!!!"):
        assert match_criterion(name, weights) is None, name


def test_match_prefers_longest_key():
    weights = {"Target": 10, "Target fit": 20}
    assert match_criterion("Target fit for the role", weights) == "Target fit"
    assert match_criterion("Target audience", weights) == "Target"


def test_match_key_is_used_once():
    """A used key is removed from weights, so a second occurrence no longer matches."""
    weights = {"Target fit": 25, "Impact bullets": 20}
    assert match_criterion("Target fit", weights) == "Target fit"
    del weights["Target fit"]
    assert match_criterion("Target fit again", weights) is None


def test_match_bounds_huge_input():
    weights = load_weights("other")
    assert match_criterion("target fit " + "x" * 100_000, weights) == "Target fit"


# --- weights ---


def test_weights_file_is_consistent_with_rubric():
    general = (RUBRICS / "general.md").read_text(encoding="utf-8")
    for program in list(PROGRAMS) + ["default"]:
        weights = load_weights(program)
        assert sum(weights.values()) == 100, program
        assert list(weights) == DEFAULT_KEYS, program
        for key in weights:
            assert f"**{key}**" in general, key


def test_weights_aliases():
    raw = json.loads((RUBRICS / "weights.json").read_text(encoding="utf-8"))
    assert raw["artificial_intelligence"] == "software_engineering"
    assert raw["psychology"] == "law"
    assert load_weights("artificial_intelligence") == load_weights("software_engineering")
    assert load_weights("psychology") == load_weights("law")
    assert load_weights("software_engineering")["Evidence and numbers"] == 20
    assert load_weights("law")["Language quality"] == 15


def test_weights_unknown_program_uses_default():
    default = load_weights("default")
    assert default["Target fit"] == 25
    for program in ("economics_big_data", "other", "no_such_program", "", None):
        assert load_weights(program) == default


def test_weights_returns_a_fresh_dict():
    first = load_weights("law")
    first.clear()
    assert load_weights("law")


def test_weights_alias_loop_and_bad_entries(tmp_path):
    path = tmp_path / "weights.json"
    path.write_text(json.dumps({
        "default": {"A": 3, "B": 0, "C": -1, "D": True, "E": 1.5, "F": 7},
        "loop_a": "loop_b",
        "loop_b": "loop_a",
        "dangling": "missing",
        "empty": {},
        "broken": [1, 2],
    }), encoding="utf-8")
    expected = {"A": 3, "F": 7}
    for program in ("loop_a", "dangling", "empty", "broken", "other"):
        assert load_weights(program, path) == expected, program


def test_weights_missing_or_corrupt_file_falls_back(tmp_path):
    builtin = load_weights("default")
    assert load_weights("law", tmp_path / "nope.json") == builtin
    bad = tmp_path / "weights.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_weights("law", bad) == builtin
    bad.write_text("[1, 2, 3]", encoding="utf-8")
    assert load_weights("law", bad) == builtin


# --- formula ---


def test_compute_score_on_demo_is_deterministic():
    analysis = demo_analysis()
    first = compute_score(analysis, None, "economics_big_data")
    second = compute_score(analysis, None, "economics_big_data")
    assert first == second
    assert analysis.overall_score == 58  # the analysis is not modified
    # criteria: (25*2 + 20*1 + 15*1 + 10*3 + 10*3 + 10*2 + 10*3) / 4 = 48.75 -> 49; 0.7*49 + 0.3*58 = 51.7 -> 52
    assert first.criteria_score == 49
    assert first.model_score == 58
    assert first.penalty == 0
    assert first.matched == 7
    assert first.score == 52
    assert first.items[0] == ("Target fit", 25, 3)
    assert [i[0] for i in first.items] == DEFAULT_KEYS


def test_compute_score_uses_program_weights():
    analysis = demo_analysis()
    # software_engineering: (20*2 + 20*1 + 20*1 + 10*3 + 10*3 + 10*2 + 10*3) / 4 = 47.5 -> 48 (halves round up)
    detail = compute_score(analysis, None, "software_engineering")
    assert detail.criteria_score == 48
    assert detail.score == 51
    assert compute_score(analysis, None, "artificial_intelligence") == detail
    assert compute_score(analysis, None, "law").criteria_score != detail.criteria_score


@pytest.mark.parametrize("program", ["economics_big_data", "software_engineering", "law", "other"])
@pytest.mark.parametrize("delta", [-20, 20])
def test_model_score_moves_result_by_at_most_six(program, delta):
    base = demo_analysis()
    shifted = base.model_copy(update={"overall_score": base.overall_score + delta})
    a = compute_score(base, None, program)
    b = compute_score(shifted, None, program)
    assert a.criteria_score == b.criteria_score
    assert abs(a.score - b.score) <= 6
    assert abs(a.score - b.score) == 6  # without clamping the shift is exact


def test_model_score_shift_with_penalty_stays_within_six(stub_checks):
    base = demo_analysis()
    report = _report("high", "medium")
    for delta in (-20, 20):
        shifted = base.model_copy(update={"overall_score": base.overall_score + delta})
        a = compute_score(base, report, "other")
        b = compute_score(shifted, report, "other")
        assert abs(a.score - b.score) <= 6


def test_model_weight_comes_from_config(monkeypatch):
    analysis = demo_analysis()
    monkeypatch.setattr(config, "SCORE_MODEL_WEIGHT", 0.0)
    assert compute_score(analysis, None, "other").score == 49  # criteria only
    monkeypatch.setattr(config, "SCORE_MODEL_WEIGHT", 1.0)
    assert compute_score(analysis, None, "other").score == 58  # model only
    monkeypatch.setattr(config, "SCORE_MODEL_WEIGHT", "junk")
    assert compute_score(analysis, None, "other").score == 52  # an invalid value means 0.3


def test_missing_config_values_use_defaults(monkeypatch):
    monkeypatch.delattr(config, "SCORE_MODEL_WEIGHT", raising=False)
    monkeypatch.delattr(config, "PENALTY_CAP", raising=False)
    assert compute_score(demo_analysis(), None, "other").score == 52
    assert compute_score(demo_analysis(), _report(*["high"] * 10), "other").penalty == 15


# --- fallback path: too few criteria ---


def test_fewer_than_four_matched_falls_back_to_model_score(stub_checks):
    grades = [("Target fit", 1), ("Impact bullets", 1), ("Evidence and numbers", 1),
              ("Creativity", 5), ("Overall vibe", 5)]
    analysis = _analysis(grades, overall=70)
    detail = compute_score(analysis, _report("high", "low"), "other")
    assert MIN_MATCHED == 4
    assert detail.matched == 3
    assert detail.penalty == 5
    assert detail.model_score == 70
    assert detail.criteria_score == 70
    assert detail.score == 65  # 70 - 5, the criteria are not taken into account
    assert len(detail.items) == 3


def test_exactly_four_matched_uses_weighted_formula():
    grades = [("Target fit", 5), ("Impact bullets", 5), ("Evidence and numbers", 5),
              ("Structure and scannability", 5), ("Creativity", 1)]
    detail = compute_score(_analysis(grades, overall=40), None, "other")
    assert detail.matched == 4
    assert detail.criteria_score == 100
    assert detail.score == 82  # 0.7*100 + 0.3*40


def test_no_matched_criteria_and_empty_scores():
    detail = compute_score(_analysis([("Creativity", 5)], overall=61), None, "other")
    assert (detail.matched, detail.items, detail.score, detail.criteria_score) == (0, [], 61, 61)
    detail = compute_score(_analysis([], overall=61), _report("medium"), "other")
    assert detail.matched == 0
    assert detail.score == 59


def test_duplicate_criterion_counts_once():
    grades = [("Target fit", 5), ("Target fit for the role", 1)] + [(k, 3) for k in DEFAULT_KEYS[1:]]
    detail = compute_score(_analysis(grades), None, "other")
    assert detail.matched == 7
    assert detail.items[0] == ("Target fit", 25, 5)


# --- clamping ---


def test_clamping_to_0_100():
    top = compute_score(_uniform(5, 150), None, "other")
    assert (top.model_score, top.criteria_score, top.score) == (100, 100, 100)
    bottom = compute_score(_uniform(1, -30), _report(*["high"] * 10), "other")
    assert (bottom.model_score, bottom.criteria_score, bottom.score) == (0, 0, 0)
    # the penalty exceeds the score: we do not go below zero
    low = compute_score(_uniform(1, 5), _report(*["high"] * 10), "other")
    assert low.penalty == 15
    assert low.score == 0
    # the fallback is clamped too
    assert compute_score(_analysis([], overall=3), _report("high", "high"), "other").score == 0
    assert compute_score(_analysis([], overall=999), None, "other").score == 100


def test_out_of_range_grades_are_clamped():
    grades = [(k, 9) for k in DEFAULT_KEYS[:3]] + [(k, -2) for k in DEFAULT_KEYS[3:]]
    detail = compute_score(_analysis(grades, overall=50), None, "other")
    assert all(1 <= s <= 5 for _, _, s in detail.items)
    assert 0 <= detail.criteria_score <= 100
    # 60 of weight on fives: 100 * 60 / 100 = 60
    assert detail.criteria_score == 60


# --- penalty ---


def test_penalty_local_without_checks_penalty(stub_checks):
    assert compute_score(demo_analysis(), None, "other").penalty == 0
    assert compute_score(demo_analysis(), _report(), "other").penalty == 0
    detail = compute_score(demo_analysis(), _report("high", "medium", "low", "bogus"), "other")
    assert detail.penalty == 7
    assert detail.score == 52 - 7
    assert compute_score(demo_analysis(), _report(*["high"] * 10), "other").penalty == 15


def test_penalty_cap_comes_from_config(stub_checks, monkeypatch):
    monkeypatch.setattr(config, "PENALTY_CAP", 6)
    assert compute_score(demo_analysis(), _report(*["high"] * 10), "other").penalty == 6


def test_penalty_prefers_checks_penalty_when_available(monkeypatch):
    stub = types.ModuleType("cvmax.checks")
    seen = []

    def fake_penalty(report):
        seen.append(report)
        return 9

    stub.penalty = fake_penalty
    monkeypatch.setitem(sys.modules, "cvmax.checks", stub)
    monkeypatch.setattr(cvmax, "checks", stub, raising=False)
    report = _report("low")
    detail = compute_score(demo_analysis(), report, "other")
    assert seen == [report]
    assert detail.penalty == 9
    assert detail.score == 52 - 9


def test_penalty_falls_back_when_checks_penalty_breaks(monkeypatch):
    stub = types.ModuleType("cvmax.checks")

    def broken(report):
        raise RuntimeError("boom")

    stub.penalty = broken
    monkeypatch.setitem(sys.modules, "cvmax.checks", stub)
    monkeypatch.setattr(cvmax, "checks", stub, raising=False)
    assert compute_score(demo_analysis(), _report("high"), "other").penalty == 4


def test_penalty_matches_real_checks_module():
    checks = pytest.importorskip("cvmax.checks")
    report = checks.CheckReport(
        findings=[
            checks.Finding(code="no_email", severity="medium", message="m"),
            checks.Finding(code="no_phone", severity="low", message="m"),
            checks.Finding(code="scanned_pdf", severity="high", message="m"),
        ],
        metrics={},
    )
    detail = compute_score(demo_analysis(), report, "other")
    assert detail.penalty == checks.penalty(report) == 7


# --- serialisation ---


def test_score_detail_is_json_serializable():
    detail = compute_score(demo_analysis(), None, "law")
    data = asdict(detail)
    assert set(data) == {"score", "criteria_score", "model_score", "penalty", "matched", "items"}
    restored = json.loads(json.dumps(data))
    assert restored["score"] == detail.score
    assert restored["items"][0] == ["Target fit", 25, 3]
    assert isinstance(detail, ScoreDetail)


def test_scoring_module_exposes_weights_path():
    assert scoring.WEIGHTS_PATH.name == "weights.json"
    assert scoring.WEIGHTS_PATH.is_file()


# --- knowledge version ---


def test_knowledge_version_changes_with_weights(tmp_path):
    shutil.copytree(RUBRICS, tmp_path / "rubrics")
    weights = tmp_path / "rubrics" / "weights.json"
    assert weights.is_file()
    version.knowledge_version.cache_clear()
    v1 = version.knowledge_version(tmp_path)

    original = weights.read_text(encoding="utf-8")
    weights.write_text(original.replace('"Target fit": 25', '"Target fit": 26', 1), encoding="utf-8")
    version.knowledge_version.cache_clear()
    v2 = version.knowledge_version(tmp_path)
    assert v2 != v1

    weights.unlink()
    version.knowledge_version.cache_clear()
    assert version.knowledge_version(tmp_path) not in (v1, v2)

    weights.write_text(original, encoding="utf-8")
    version.knowledge_version.cache_clear()
    assert version.knowledge_version(tmp_path) == v1
    version.knowledge_version.cache_clear()


def test_version_patterns_include_rubric_json():
    assert "rubrics/*.json" in version._PATTERNS
