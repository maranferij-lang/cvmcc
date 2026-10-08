"""Тести бандита, подій і версії знань."""
from __future__ import annotations

import json
import random
import re

import pytest

from cvmax.learning import bandit, events, version

RATES = {"v1-baseline": 0.7, "v2-x": 0.5, "v3-y": 0.3}


@pytest.fixture
def vdir(tmp_path):
    data = {"task": "analysis", "variants": [
        {"id": i, "active": True, "text": "" if i == "v1-baseline" else i} for i in RATES]}
    (tmp_path / "analysis.json").write_text(json.dumps(data), encoding="utf-8")
    return tmp_path


def test_simulation_converges(vdir):
    rng = random.Random(42)
    counts = {v: [0, 0] for v in RATES}
    picks = []
    for _ in range(1000):
        stats = [{"variant": v, "program": "cs", "successes": s, "failures": f}
                 for v, (s, f) in counts.items()]
        v = bandit.choose_variant("analysis", "cs", stats, rng, vdir)
        picks.append(v)
        counts[v][0 if rng.random() < RATES[v] else 1] += 1
    assert picks[-500:].count("v1-baseline") > 350
    summary = bandit.summarise(
        [{"variant": v, "program": "cs", "successes": s, "failures": f}
         for v, (s, f) in counts.items()], "cs")
    assert "v3-y" in bandit.disabled_variants(summary)


def test_program_fallback():
    stats = [
        {"variant": "a", "program": "cs", "successes": 3, "failures": 2},
        {"variant": "a", "program": "law", "successes": 10, "failures": 5},
        {"variant": "b", "program": "cs", "successes": 8, "failures": 4},
        {"variant": "b", "program": "law", "successes": 1, "failures": 1},
    ]
    s = bandit.summarise(stats, "cs")
    assert s["a"] == (13, 7)
    assert s["b"] == (8, 4)


def test_never_disable_only_one():
    assert bandit.disabled_variants({"a": (1, 40)}) == set()


def test_loading_and_fallbacks(tmp_path, vdir):
    assert bandit.load_variants("nope", tmp_path) == []
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    assert bandit.load_variants("bad", tmp_path) == []
    assert bandit.variant_text("analysis", "v2-x", vdir) == "v2-x"
    assert bandit.variant_text("analysis", "zzz", vdir) == ""
    assert bandit.choose_variant("nope", "cs", [], directory=tmp_path) == ""


def test_payload():
    p = events.make_payload(events.ANALYSIS_RATED, analysis_id="x" * 500, rating=1,
                            cv_text="secret", obj={"a": 1})
    assert set(p) == {"analysis_id", "rating"}
    assert len(p["analysis_id"]) == 200
    with pytest.raises(ValueError):
        events.make_payload("nope")
    with pytest.raises(ValueError):
        events.make_payload(events.JOBS_SHOWN, sources=["x" * 200] * 30)


def test_knowledge_version(tmp_path):
    (tmp_path / "rubrics").mkdir()
    f = tmp_path / "rubrics" / "a.md"
    f.write_text("one", encoding="utf-8")
    version.knowledge_version.cache_clear()
    v1 = version.knowledge_version(tmp_path)
    assert re.fullmatch(r"[0-9a-f]{12}", v1)
    f.write_text("two", encoding="utf-8")
    version.knowledge_version.cache_clear()
    assert version.knowledge_version(tmp_path) != v1


def test_inactive_variant_does_not_disable_active(tmp_path):
    """Неактивний варіант з високим середнім не вимикає всіх активних."""
    data = {"task": "analysis", "variants": [
        {"id": "v1-baseline", "active": True, "text": ""},
        {"id": "v2", "active": True, "text": "v2"},
        {"id": "v3", "active": True, "text": "v3"},
        {"id": "v4", "active": False, "text": "v4"}]}
    (tmp_path / "analysis.json").write_text(json.dumps(data), encoding="utf-8")
    stats = [{"variant": v, "program": "cs", "successes": s, "failures": 40 - s}
             for v, s in [("v4", 32), ("v1-baseline", 25), ("v2", 24), ("v3", 24)]]
    rng = random.Random(1)
    picks = {bandit.choose_variant("analysis", "cs", stats, rng, tmp_path) for _ in range(200)}
    assert picks == {"v1-baseline", "v2", "v3"}
