"""Відповідь про співбесіду з кабінету доходить до бандита (петля D)."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import ui.account as ua
from cvmax.db import MemoryDB

PAGE = str(Path(__file__).resolve().parents[1] / "views" / "account.py")
SCRIPT = f"exec(compile(open({PAGE!r}, encoding='utf-8').read(), {PAGE!r}, 'exec'))"
RID = "11111111-1111-1111-1111-111111111111"


class FakeDB(MemoryDB):
    persistent = True

    def __init__(self, payload: dict):
        super().__init__()
        self._payload = payload

    def list_results(self, email, limit=50):
        return [{"id": RID, "kind": "analysis", "title": "BA · 70/100", "created_at": "2020-01-01T00:00:00Z"}]

    def get_result(self, email, result_id):
        return {"id": result_id, "kind": "analysis", "payload": self._payload}


def _run(monkeypatch, db):
    monkeypatch.setattr(ua, "get_db", lambda: db)
    monkeypatch.setattr(ua, "current_email", lambda: "student@uni.edu")
    monkeypatch.setattr(ua, "user_row", lambda: {})
    at = AppTest.from_string(SCRIPT, default_timeout=60)
    at.run()
    return at


def _seed_done(db, aid):
    db.log_event("u", "analysis_done", {"analysis_id": aid, "task": "analysis", "variant": "v1", "program": "p"})


def test_outcome_yes_rewards_variant(monkeypatch):
    db = FakeDB({"analysis_id": "a1"})
    _seed_done(db, "a1")
    before = {r["variant"]: r["successes"] for r in db.variant_stats("analysis")}
    at = _run(monkeypatch, db)
    at.button_group(key=f"outcome_{RID}").set_value("Yes").run()
    assert not at.exception
    out = [e for e in db.events if e["kind"] == "outcome"]
    assert out and out[0]["payload"]["analysis_id"] == "a1"
    after = {r["variant"]: r["successes"] for r in db.variant_stats("analysis")}
    assert after["v1"] == before.get("v1", 0) + 2


def test_old_result_without_analysis_id_hides_control(monkeypatch):
    at = _run(monkeypatch, FakeDB({}))
    assert not at.exception
    assert not at.button_group
