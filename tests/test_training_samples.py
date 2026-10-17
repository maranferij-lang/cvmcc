"""Opt-in anonymised samples: the DB methods (MemoryDB, SupabaseDB request shape) and the consent flow."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from cvmax.db import MemoryDB, SupabaseDB

APP = str(Path(__file__).resolve().parents[1] / "app.py")
META = {"program": "Business", "level": "Junior", "region": "EU", "target_role": "Analyst"}


def sample(db, email="ann@example.com", cv="Led a team of 5.", analysis=None):
    return db.save_training_sample(email, "2026-10-17", META, cv, analysis or {"summary": "ok"})


def test_save_and_list_for_judge_newest_first_and_capped():
    db = MemoryDB()
    ids = [sample(db, cv=f"cv {i}") for i in range(3)]
    db.training_samples[0]["created_at"] -= timedelta(hours=2)
    got = db.training_samples_for_judge(days=7, limit=20)
    assert [g["id"] for g in got] == [ids[2], ids[1], ids[0]]
    assert set(got[0]) == {"id", "meta", "cv_text", "analysis"}
    assert len(db.training_samples_for_judge(limit=1)) == 1
    for _ in range(60):
        sample(db)
    assert len(db.training_samples_for_judge(limit=500)) == 50


def test_old_samples_are_not_offered_for_judge():
    db = MemoryDB()
    sample(db)
    db.training_samples[0]["created_at"] = datetime.now(timezone.utc) - timedelta(days=10)
    assert db.training_samples_for_judge(days=7) == []
    assert len(db.training_samples_for_judge(days=30)) == 1


def test_judgement_moves_a_sample_from_unjudged_to_judged():
    db = MemoryDB()
    sid = sample(db)
    db.save_judgement(sid, {"quality": 4, "findings": []})
    assert db.training_samples_for_judge() == []
    (row,) = db.judged_samples()
    assert row["id"] == sid and row["judge"]["quality"] == 4 and row["cv_text"] == "Led a team of 5."
    assert len(db.judged_samples(limit=0)) == 1  # the limit is clamped to 1..200, never raises


def test_returned_rows_are_copies():
    db = MemoryDB()
    sample(db)
    db.training_samples_for_judge()[0]["analysis"]["summary"] = "changed"
    assert db.training_samples[0]["analysis"]["summary"] == "ok"


def test_purge_removes_only_rows_older_than_12_months():
    db = MemoryDB()
    old, new = sample(db), sample(db)
    db.training_samples[0]["created_at"] = datetime.now(timezone.utc) - timedelta(days=400)
    assert db.purge_training_samples() == 1
    assert [x["id"] for x in db.training_samples] == [new] and old != new
    assert db.purge_training_samples() == 0


def test_delete_my_data_removes_the_users_samples_only():
    db = MemoryDB()
    sample(db, email="Ann@Example.com")
    keep = sample(db, email="bob@example.com")
    db.delete_my_data("ann@example.com")
    assert [x["id"] for x in db.training_samples] == [keep]


@pytest.mark.parametrize("kwargs", [
    {"cv": ""}, {"cv": "   "}, {"cv": "x" * 40001}, {"analysis": {"a": "x" * 300001}},
])
def test_oversize_or_empty_input_is_rejected(kwargs):
    with pytest.raises(ValueError):
        sample(MemoryDB(), **kwargs)


def test_oversize_judgement_is_rejected():
    db = MemoryDB()
    sid = sample(db)
    with pytest.raises(ValueError):
        db.save_judgement(sid, {"x": "y" * 100001})


class FakeSession:
    def __init__(self, reply=None):
        self.calls, self.reply = [], reply

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url.rsplit("/", 1)[-1], json))

        class R:
            status_code, content = 200, b"1"
            def json(_self):
                return self.reply
        return R()


def test_supabase_methods_call_the_right_functions():
    http = FakeSession(reply="abc")
    db = SupabaseDB("https://x.example", "key", "tok", session=http)
    assert db.save_training_sample("a@example.com", "v", META, "cv", {"s": 1}) == "abc"
    db.training_samples_for_judge(3, 5)
    db.save_judgement("abc", {"quality": 3})
    db.judged_samples(30, 10)
    db.purge_training_samples()
    names = [c[0] for c in http.calls]
    assert names == ["cvmax_save_training_sample", "cvmax_training_samples_for_judge", "cvmax_save_judgement",
                     "cvmax_judged_samples", "cvmax_purge_training_samples"]
    assert http.calls[0][1]["p_consent_version"] == "v" and http.calls[1][1]["p_limit"] == 5
    assert all(c[1]["p_token"] == "tok" for c in http.calls)


def test_supabase_checks_sizes_before_the_network():
    http = FakeSession()
    db = SupabaseDB("https://x.example", "key", "tok", session=http)
    with pytest.raises(ValueError):
        db.save_training_sample("a@example.com", "v", META, "x" * 40001, {})
    assert http.calls == []


# ---------- Consent flow on the review page ----------

CV_WITH_CONTACTS = "Jane Example\njane.example@example.com +1 555 010 1234\nlinkedin.com/in/jane-example\nLed a team."


@pytest.fixture
def signed_in(monkeypatch):
    import ui.account
    import ui.common

    monkeypatch.setenv("CVMAX_DEMO", "1")
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(ui.account, "is_logged_in", lambda: True)
    monkeypatch.setattr(ui.account, "current_email", lambda: "jane.example@example.com")
    monkeypatch.setattr(ui.account, "current_name", lambda: "Jane Example")
    monkeypatch.setattr(ui.account, "needs_onboarding", lambda: False)
    monkeypatch.setattr(ui.common, "DEMO_CV_TEXT", CV_WITH_CONTACTS)


def run_review(consent):
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.switch_page("views/analyze.py").run()
    at.checkbox(key="consent_analyze").check().run()
    at.text_input(key="target_role").input("Business Analyst").run()
    box = at.checkbox(key="improve_consent")
    assert box.value is False  # default off
    if consent:
        box.check().run()
    next(b for b in at.button if b.label == "Review my CV").click().run()
    assert not at.exception
    return at


def test_unticked_saves_nothing(signed_in):
    from ui.account import get_db

    run_review(consent=False)
    assert get_db().training_samples == []


def test_ticked_saves_an_anonymised_copy(signed_in):
    import json

    from ui.account import get_db

    run_review(consent=True)
    (row,) = get_db().training_samples
    blob = json.dumps(row, default=str)
    assert row["user_key"] == "jane.example@example.com" and row["consent_version"] == "2026-10-17"
    for secret in ("jane.example@example.com", "Jane", "Example", "linkedin", "555"):
        assert secret not in row["cv_text"] and secret not in json.dumps(row["analysis"])
    assert "[name]" in row["cv_text"] and "[email]" in row["cv_text"]
    assert row["meta"]["target_role"] == "Business Analyst" and row["meta"]["prompt_variant"]
    assert "jane.example@example.com" not in blob.replace(row["user_key"], "")


def test_a_failing_save_does_not_break_the_review(signed_in, monkeypatch):
    from cvmax.db import MemoryDB

    def boom(self, *a, **k):
        raise RuntimeError("secret cv text in message")
    monkeypatch.setattr(MemoryDB, "save_training_sample", boom)
    at = run_review(consent=True)
    assert at.session_state["analysis"] is not None


def test_checkbox_is_hidden_when_signed_out(monkeypatch):
    monkeypatch.setenv("CVMAX_DEMO", "1")
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.switch_page("views/analyze.py").run()
    at.checkbox(key="consent_analyze").check().run()
    assert not [c for c in at.checkbox if c.key == "improve_consent"]


def test_sql_save_function_purges_old_rows_before_insert():
    root = Path(__file__).resolve().parents[1] / "supabase"
    for name in ("apply-judge.sql", "schema.sql"):
        text = (root / name).read_text()
        start = text.index("function public.cvmax_save_training_sample(")
        body = text[start:text.index("end $$;", start)]
        purge = body.index("delete from public.cvmax_training_samples where created_at < now() - interval '12 months'")
        assert purge < body.index("insert into public.cvmax_training_samples"), name


def test_consent_caption_and_privacy_texts_are_honest():
    root = Path(__file__).resolve().parents[1]
    analyze = (root / "views" / "analyze.py").read_text().replace('\\"', '"')
    assert "Unticking stops saving new copies" in analyze and '"Delete my data" on My account' in analyze
    for name in ("site/privacy.html", "views/privacy.py"):
        import re

        text = re.sub(r'"\s*"', "", (root / name).read_text())  # join adjacent string literals
        text = " ".join(text.split())
        for needle in ("name at the top of your CV", "messenger handles", "dates of birth", "employers",
                       "kept up to 12 months; older copies are deleted"):
            assert needle in text, (name, needle)
