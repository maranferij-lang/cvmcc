"""Paywall in the app: the free scan renders only the free parts, unlock and the return from checkout (docs/plan-paywall.md)."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import cvmax.demo as demo
import ui.account as ua
import ui.billing as ub
from cvmax.billing import FakePaddle
from cvmax.db import MemoryDB
from cvmax.schemas import Analysis, Edit

ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "app.py")
ME = "jane.example@example.com"
FOURTH = "ZEBRA fourth edit text only the full fix may show"


_ORIGINAL = demo.demo_analysis


def _four_edit_analysis() -> Analysis:
    a = _ORIGINAL()
    extra = Edit(section="Activities", before="Member of Student Council", after=FOURTH,
                 reason="Lowest priority, locked in the scan.", priority="low")
    return a.model_copy(update={"edits": [*a.edits, extra]})


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("CVMAX_DEMO", "1")
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(demo, "demo_analysis", _four_edit_analysis)
    monkeypatch.setattr(ua, "is_logged_in", lambda: True)
    monkeypatch.setattr(ua, "current_email", lambda: ME)
    monkeypatch.setattr(ua, "current_name", lambda: "Jane Example")
    monkeypatch.setattr(ua, "needs_onboarding", lambda: False)


@pytest.fixture
def billing_on(env, monkeypatch):
    monkeypatch.setenv("PADDLE_API_KEY", "test_key_not_real")
    monkeypatch.setenv("PADDLE_PRICE_FULL", "pri_fakefull")
    monkeypatch.setenv("PADDLE_PRICE_HUNT", "pri_fakehunt")
    monkeypatch.setattr(ua, "auth_configured", lambda: True)
    fake = FakePaddle()
    monkeypatch.setattr(ub, "get_paddle", lambda: fake)
    return fake


def _tree_text(node) -> str:
    """All text of the rendered element tree (the protos hold every label and body)."""
    out = []
    for child in node.children.values():
        proto = getattr(child, "proto", None)
        if proto is not None:
            out.append(str(proto))
        if hasattr(child, "children"):
            out.append(_tree_text(child))
    return "\n".join(out)


def _review(at=None):
    at = at or AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.switch_page("views/analyze.py").run()
    at.checkbox(key="consent_analyze").check().run()
    at.text_input(key="target_role").input("Business Analyst").run()
    next(b for b in at.button if b.label == "Review my CV").click().run()
    assert not at.exception
    return at


def _button(at, label):
    return next((b for b in at.button if b.label == label), None)


def test_billing_off_is_unchanged(env):
    at = _review()
    text = _tree_text(at.main)
    assert FOURTH in text
    assert _button(at, "Get Full fix") is None
    assert at.session_state["tier"] == "full"
    assert any(str(c.key).startswith("accept_") for c in at.checkbox)


def test_scan_hides_locked_edits(billing_on):
    at = _review()
    assert at.session_state["tier"] == "scan"
    text = _tree_text(at.main)
    assert FOURTH not in text
    assert "weekly Excel reports" in text and "joins, GROUP BY, window" in text
    assert "1 more edit is locked" in text
    assert any(s.value == "Unlock the full fix" for s in at.subheader)
    assert _button(at, "Get Full fix") is not None and _button(at, "Get Job hunt") is not None
    assert not [c for c in at.checkbox if str(c.key).startswith("accept_")]
    assert "Start Q&A" not in [b.label for b in at.button]


def test_unlock_with_credit_spends_exactly_one(billing_on):
    db = ua.get_db()
    db.grant_purchase(ME, "hunt", "txn_seed1")  # 5 credits
    at = _review()
    _button(at, "Unlock with 1 credit").click().run()
    assert not at.exception
    assert at.session_state["tier"] == "full"
    assert FOURTH in _tree_text(at.main)
    assert db.credits(ME)["full"] == 4
    assert len(db.credit_uses) == 1
    at.run()  # a rerun never spends again
    assert len(db.credit_uses) == 1 and db.credits(ME)["full"] == 4


def test_checkout_started_logs_only_plan_and_source(billing_on):
    at = _review()
    _button(at, "Get Full fix").click().run()
    assert not at.exception
    assert at.session_state["pending_txn"].startswith("txn_fake")
    assert at.session_state["pending_analysis"] == at.session_state["analysis_id"]
    assert [el.proto.label for el in at.get("link_button") if "checkout" in el.proto.label] == ["Continue to secure checkout"]
    events = [e for e in ua.get_db().events if e["kind"] == "checkout_started"]
    assert [e["payload"] for e in events] == [{"plan": "full", "source": "analyze"}]


def test_return_grants_and_unlocks(billing_on):
    at = _review()
    _button(at, "Get Full fix").click().run()
    at.query_params["paid"] = "1"
    at.run()
    assert not at.exception
    db = ua.get_db()
    assert [p["plan"] for p in db.purchases] == ["full"]
    assert len(db.credit_uses) == 1 and db.credits(ME)["full"] == 0
    assert at.session_state["tier"] == "full"
    assert "pending_txn" not in at.session_state
    assert FOURTH in _tree_text(at.main)
    at.run()
    assert len(db.credit_uses) == 1 and len(db.purchases) == 1


def test_return_with_another_users_transaction_does_not_grant(billing_on):
    at = _review()
    other = billing_on.create_checkout("full", "someone.else@example.com")
    at.session_state["pending_txn"] = other.transaction_id
    at.session_state["pending_analysis"] = at.session_state["analysis_id"]
    at.query_params["paid"] = "1"
    at.run()
    assert not at.exception
    db = ua.get_db()
    assert db.purchases == [] and db.credit_uses == []
    assert at.session_state["tier"] == "scan"
    assert FOURTH not in _tree_text(at.main)


def test_return_without_pending_shows_account_hint(billing_on):
    at = _review()
    at.query_params["paid"] = "1"
    at.run()
    assert not at.exception
    assert any("Payment received?" in i.value for i in at.info)
    assert ua.get_db().purchases == []


def test_return_while_payment_is_processing_keeps_pending(billing_on):
    billing_on.auto_complete = False
    at = _review()
    _button(at, "Get Full fix").click().run()
    at.query_params["paid"] = "1"
    at.run()
    assert not at.exception
    assert ua.get_db().purchases == [] and "pending_txn" in at.session_state


class _SavedDB(MemoryDB):
    persistent = True

    def __init__(self, payload):
        super().__init__()
        self.payload = payload

    def list_results(self, email, limit=50):
        return [{"id": "r1", "kind": "analysis", "title": "BA · 58/100", "created_at": "2026-10-01T00:00:00Z"}]

    def get_result(self, email, result_id):
        return {"id": result_id, "kind": "analysis", "payload": self.payload}


def _account(monkeypatch, db):
    monkeypatch.setattr(ua, "get_db", lambda: db)
    monkeypatch.setattr(ua, "user_row", lambda: {})
    at = AppTest.from_file(str(ROOT / "views" / "account.py"), default_timeout=60)
    at.run()
    at.toggle(key="show_r1").set_value(True).run()
    assert not at.exception
    return at


def test_saved_full_result_reopens_without_spending(billing_on, monkeypatch):
    payload = {"analysis_id": "a1", "role": "Business Analyst", "tier": "full",
               "analysis": _four_edit_analysis().model_dump()}
    db = _SavedDB(payload)
    at = _account(monkeypatch, db)
    assert FOURTH in _tree_text(at.main)
    assert db.credit_uses == []
    assert not [b for b in at.button if b.label == "Unlock the full fix"]


def test_saved_scan_result_stays_locked_until_unlocked(billing_on, monkeypatch):
    payload = {"analysis_id": "a2", "role": "Business Analyst", "tier": "scan",
               "analysis": _four_edit_analysis().model_dump()}
    db = _SavedDB(payload)
    db.grant_purchase(ME, "full", "txn_seed2")
    at = _account(monkeypatch, db)
    assert FOURTH not in _tree_text(at.main)
    _button(at, "Unlock the full fix").click().run()
    assert FOURTH in _tree_text(at.main)
    assert len(db.credit_uses) == 1 and db.credits(ME)["full"] == 0


def test_where_to_apply_needs_hunt(billing_on):
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.switch_page("views/career.py").run()
    assert not at.exception
    assert _button(at, "Find directions") is None
    assert _button(at, "Get Job hunt") is not None and _button(at, "Get Full fix") is None
    ua.get_db().grant_purchase(ME, "hunt", "txn_seed3")
    at.run()
    at.checkbox(key="consent_career").check().run()
    assert _button(at, "Find directions") is not None


def test_billing_needs_sign_in_configured(env, monkeypatch):
    monkeypatch.setenv("PADDLE_API_KEY", "k")
    monkeypatch.setenv("PADDLE_PRICE_FULL", "pri_fakefull")
    monkeypatch.setenv("PADDLE_PRICE_HUNT", "pri_fakehunt")
    monkeypatch.setattr(ua, "auth_configured", lambda: False)
    assert ub.billing_enabled() is False


# --- fixes after review: builder credits, return across tabs, Jobs tab, re-scan fallback ---

def _builder_at():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.switch_page("views/builder.py").run()
    for c in at.checkbox:
        c.check()
    at.run()
    at.text_input[0].input("Alex Example")
    at.text_area[0].input("I worked as an intern at an example company doing data analysis in Excel and SQL for a year.")
    at.run()
    _button(at, "Start").click().run()
    return at


def test_builder_uses_one_credit_per_build(billing_on):
    db = ua.get_db()
    db.grant_purchase(ME, "full", "txn_seedb1")
    at = _builder_at()
    assert not at.exception
    _button(at, "Build my CV").click().run()
    assert not at.exception and at.session_state["builder_cv"] is not None
    assert len(db.credit_uses) == 1 and db.credits(ME)["full"] == 0
    assert db.credit_uses[0]["purpose"] == "builder" and db.credit_uses[0]["rescans_left"] == 0
    assert "builder_paid" not in at.session_state
    first_cv = at.session_state["builder_cv"]
    at.run()  # the button's type changes once the interview is finished
    _button(at, "Build my CV").click().run()  # no credit left: no second build
    assert not at.exception
    assert len(db.credit_uses) == 1 and at.session_state["builder_cv"] is first_cv
    assert _button(at, "Get Full fix") is not None  # never a dead end
    _button(at, "Start over").click().run()
    assert "builder_cv" not in at.session_state
    assert any(s.value == "The CV builder is part of the full fix" for s in at.subheader)


def test_builder_gate_ignores_a_stale_flag(billing_on):
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.session_state["builder_paid"] = True
    at.switch_page("views/builder.py").run()
    assert not at.exception
    assert any(s.value == "The CV builder is part of the full fix" for s in at.subheader)


def test_builder_credit_does_not_give_a_review_rescan(billing_on):
    db = ua.get_db()
    db.grant_purchase(ME, "hunt", "txn_seedb2")
    assert db.use_credit(ME, "builder-1", "Business Analyst", purpose="builder")["mode"] == "credit"
    at = _review()
    _button(at, "Unlock with 1 credit").click().run()
    assert not at.exception
    assert [u["mode"] for u in db.credit_uses] == ["credit", "credit"] and db.credits(ME)["full"] == 3


def test_return_from_another_tab_uses_txn_and_unlocks_saved_review(billing_on, monkeypatch):
    payload = {"analysis_id": "saved-a1", "role": "Business Analyst", "tier": "scan",
               "analysis": _four_edit_analysis().model_dump()}
    db = _SavedDB(payload)
    monkeypatch.setattr(ua, "get_db", lambda: db)
    link = billing_on.create_checkout("full", ME, ME, "saved-a1")
    at = AppTest.from_file(APP, default_timeout=60)
    at.query_params["paid"] = "1"
    at.query_params["txn"] = link.transaction_id
    at.run()
    assert not at.exception
    assert [p["transaction_id"] for p in db.purchases] == [link.transaction_id]
    assert [u["analysis_id"] for u in db.credit_uses] == ["saved-a1"] and db.credits(ME)["full"] == 0
    assert "paid" not in at.query_params and "txn" not in at.query_params


def test_return_does_not_unlock_an_analysis_of_someone_else(billing_on, monkeypatch):
    db = _SavedDB({"analysis_id": "mine", "role": "BA", "tier": "scan", "analysis": _four_edit_analysis().model_dump()})
    monkeypatch.setattr(ua, "get_db", lambda: db)
    link = billing_on.create_checkout("full", ME, ME, "someone-elses-analysis")
    at = AppTest.from_file(APP, default_timeout=60)
    at.query_params["paid"] = "1"
    at.query_params["txn"] = link.transaction_id
    at.run()
    assert not at.exception
    assert len(db.purchases) == 1 and db.credit_uses == [] and db.credits(ME)["full"] == 1


def test_return_with_another_users_txn_in_url_grants_nothing(billing_on):
    other = billing_on.create_checkout("full", "someone.else@example.com", "", "x1")
    at = AppTest.from_file(APP, default_timeout=60)
    at.query_params["paid"] = "1"
    at.query_params["txn"] = other.transaction_id
    at.run()
    assert not at.exception
    db = ua.get_db()
    assert db.purchases == [] and db.credit_uses == []


def test_return_ignores_a_malformed_txn(billing_on):
    at = _review()
    at.query_params["paid"] = "1"
    at.query_params["txn"] = "txn_../../x"
    at.run()
    assert not at.exception
    assert ua.get_db().purchases == []
    assert any("Payment received?" in i.value for i in at.info)


def test_return_processing_shows_calm_message_and_refresh(billing_on):
    billing_on.auto_complete = False
    link = billing_on.create_checkout("full", ME, ME, "x1")
    at = _review()
    at.query_params["paid"] = "1"
    at.query_params["txn"] = link.transaction_id
    at.run()
    assert not at.exception
    assert any("still processing" in i.value and "Refresh in a minute" in i.value for i in at.info)
    assert _button(at, "Refresh") is not None
    assert ua.get_db().purchases == []
    billing_on.complete(link.transaction_id)
    _button(at, "Refresh").click().run()
    assert not at.exception
    assert len(ua.get_db().purchases) == 1


def test_jobs_tab_gets_no_locked_gaps_in_scan_tier(billing_on, monkeypatch):
    import ui.jobs as jobs
    seen = []
    monkeypatch.setattr(jobs, "render_jobs", lambda **kw: seen.append(kw["gaps"]))
    _review()
    assert seen and all(g == [] for g in seen)


def test_jobs_tab_gets_gaps_when_unlocked(billing_on, monkeypatch):
    import ui.jobs as jobs
    seen = []
    monkeypatch.setattr(jobs, "render_jobs", lambda **kw: seen.append(kw["gaps"]))
    ua.get_db().grant_purchase(ME, "full", "txn_seedj")
    at = _review()
    _button(at, "Unlock with 1 credit").click().run()
    assert seen[-1]


def test_expected_rescan_but_db_says_no_credit_shows_plans(billing_on, monkeypatch):
    db = ua.get_db()
    real = db.credits
    monkeypatch.setattr(db, "credits", lambda k: {**real(k), "rescan_roles": ["business analyst"]})
    at = _review()
    assert _button(at, "Get Full fix") is None  # the UI believes a free re-scan exists
    _button(at, "Unlock with my free re-scan").click().run()
    assert not at.exception
    assert _button(at, "Get Full fix") is not None and _button(at, "Get Job hunt") is not None
    assert db.credit_uses == []
