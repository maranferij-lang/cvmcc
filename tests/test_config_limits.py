"""CVMAX_LIMITS overrides for the daily limits."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from cvmax import config

ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "app.py")


def base():
    return {"analysis": (5, 60), "jobs": (10, 300)}


def test_override_sets_user_and_site_limits():
    limits = base()
    assert config.apply_limit_overrides(limits, "analysis=10:200, jobs=20:600") == []
    assert limits == {"analysis": (10, 200), "jobs": (20, 600)}


def test_none_means_no_limit_on_that_side():
    limits = base()
    config.apply_limit_overrides(limits, "analysis=5:none")
    assert limits["analysis"] == (5, None)


def test_bad_entries_are_ignored_and_reported():
    limits = base()
    ignored = config.apply_limit_overrides(
        limits, "nope=1:2,analysis=abc:5,jobs=3,analysis=-1:5,jobs=5:99999999999")
    assert limits == base()
    assert len(ignored) == 5


def test_good_entries_survive_next_to_bad_ones():
    limits = base()
    ignored = config.apply_limit_overrides(limits, "jobs=x:y,analysis=7:70")
    assert limits["analysis"] == (7, 70) and limits["jobs"] == (10, 300)
    assert ignored == ["jobs=x:y"]


def test_empty_and_missing_values_change_nothing():
    for raw in (None, "", " , "):
        limits = base()
        assert config.apply_limit_overrides(limits, raw) == []
        assert limits == base()


def test_every_real_limit_kind_is_overridable():
    limits = dict(config.LIMITS)
    raw = ",".join(f"{kind}=1:2" for kind in limits)
    assert config.apply_limit_overrides(limits, raw) == []
    assert all(v == (1, 2) for v in limits.values())


def test_kind_names_are_case_and_space_insensitive():
    limits = base()
    ignored = config.apply_limit_overrides(limits, "Jobs=20:600, ANALYSIS =7:none")
    assert ignored == []
    assert limits == {"analysis": (7, None), "jobs": (20, 600)}


def test_ignored_entries_are_logged_once_at_import():
    """Runs a fresh interpreter, because the log happens when cvmax.config is imported."""
    long_entry = "analysis=" + "9" * 100  # a malformed entry longer than 60 characters
    env = {
        **os.environ,
        "CVMAX_LIMITS": f"nope=1:2,jobs=20:600,{long_entry}",
        "CVMAX_MODEL": "other-env-value-that-must-not-be-logged",
    }
    out = subprocess.run(
        [sys.executable, "-c", "import cvmax.config as c; print(c.LIMITS['jobs'])"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "(20, 600)"  # the good entry still applied
    lines = [line for line in out.stderr.splitlines() if "CVMAX_LIMITS" in line]
    assert len(lines) == 2
    assert any("nope=1:2" in line for line in lines)
    assert not any("9" * 61 in line for line in lines)  # cut to 60 characters
    assert "other-env-value-that-must-not-be-logged" not in out.stderr
    assert "jobs=20:600" not in out.stderr  # valid entries are not reported


def test_valid_overrides_log_nothing():
    env = {**os.environ, "CVMAX_LIMITS": "analysis=10:200,Jobs=20:none"}
    out = subprocess.run(
        [sys.executable, "-c", "import cvmax.config"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0 and "CVMAX_LIMITS" not in out.stderr


# ---------- No limit (None) must be shown in words, never as "None" ----------

@pytest.fixture
def no_limits(monkeypatch):
    monkeypatch.setenv("CVMAX_DEMO", "1")
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    for kind, (_, site) in list(config.LIMITS.items()):
        monkeypatch.setitem(config.LIMITS, kind, (None, site))


def page_text(at) -> str:
    parts = [m.value for m in at.markdown] + [c.value for c in at.caption]
    parts += [e.label for e in at.expander]
    return "\n".join(parts)


def test_home_says_no_limit_instead_of_none(no_limits):
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception
    text = page_text(at)
    assert "None" not in text
    assert "no daily limit on CV reviews per person" in text


def test_terms_say_unlimited_instead_of_none(no_limits):
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.switch_page("views/terms.py").run()
    assert not at.exception
    text = page_text(at)
    assert "None" not in text
    assert "unlimited CV reviews" in text


def test_limit_caption_says_no_limit_instead_of_none(no_limits):
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.switch_page("views/analyze.py").run()
    at.checkbox(key="consent_analyze").check().run()
    at.text_input(key="target_role").input("Business Analyst").run()
    assert not at.exception
    captions = [c.value for c in at.caption]
    assert not any("None" in c for c in captions)
    assert any("no daily limit on CV reviews" in c for c in captions)


def test_home_names_the_main_limit_and_the_smaller_ones(monkeypatch):
    monkeypatch.setenv("CVMAX_DEMO", "1")
    monkeypatch.setitem(config.LIMITS, "analysis", (7, 60))
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    text = page_text(at)
    assert "The main limit is 7 CV reviews a day per person, and there are smaller daily limits" in text
    assert "The only limit" not in text
