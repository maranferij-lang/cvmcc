"""Кожна сторінка відкривається в демо-режимі без помилок."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")
PAGES = ["views/home.py", "views/analyze.py", "views/career.py", "views/builder.py",
         "views/about.py", "views/privacy.py", "views/terms.py", "views/feedback.py"]


@pytest.fixture(autouse=True)
def demo_mode(monkeypatch):
    monkeypatch.setenv("CVMAX_DEMO", "1")
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(page):
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.switch_page(page).run()
    assert not at.exception


def test_analyze_flow_exports_pdf():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.switch_page("views/analyze.py").run()
    at.checkbox(key="consent_analyze").check().run()
    at.text_input(key="target_role").input("Business Analyst").run()
    next(b for b in at.button if b.label == "Review my CV").click().run()
    for box in at.checkbox:
        if str(box.key).startswith("accept_"):
            box.check()
    at.run()
    next(b for b in at.button if b.label.startswith("Format my CV")).click().run()
    assert not at.exception
    labels = [el.proto.label for el in at.get("download_button")]
    assert "Download PDF" in labels and "Download DOCX" in labels


def test_feedback_page_sends_anonymous_feedback():
    from ui.account import get_db

    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.switch_page("views/feedback.py").run()
    at.text_area[0].input("It told me to cut an important line").run()
    next(b for b in at.button if b.label == "Send").click().run()
    assert not at.exception
    assert any("Thank" in s.value for s in at.success)


def test_interview_asks_twice_in_a_row_and_survives_model_errors(monkeypatch):
    """Опитування в демо стартує щоразу з першого питання, а помилка моделі не лишає порожню вкладку."""
    import cvmax.grill
    from cvmax.llm import LLMError

    def start(at):
        at.switch_page("views/analyze.py").run()
        at.checkbox(key="consent_analyze").check().run()
        at.text_input(key="target_role").input("Business Analyst").run()
        next(b for b in at.button if b.label == "Review my CV").click().run()
        next(b for b in at.button if b.label == "Start Q&A").click().run()

    for _ in range(2):  # другий раз раніше давав порожню вкладку
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        start(at)
        assert not at.exception
        assert any(t.label == "Your answer" for t in at.text_area)

    def broken(*args, **kwargs):
        raise LLMError("The free AI models are overloaded right now. Try again in a minute.")

    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    monkeypatch.setattr(cvmax.grill, "ask_structured", broken)
    start(at)
    assert not at.exception
    assert any("overloaded" in e.value for e in at.error)
    assert any(b.label == "Start Q&A" for b in at.button)  # можна спробувати ще раз


def test_rating_a_review_sends_feedback():
    """Великий палець під аналізом раніше падав: локальна функція перекривала send_feedback."""
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.switch_page("views/analyze.py").run()
    at.checkbox(key="consent_analyze").check().run()
    at.text_input(key="target_role").input("Business Analyst").run()
    next(b for b in at.button if b.label == "Review my CV").click().run()
    at.session_state[f"rate_{at.session_state['analysis_id']}"] = 1  # 👍
    at.run()
    assert not at.exception
    assert any("Thanks" in t.proto.body or "Thank" in t.proto.body for t in at.toast)


def test_home_waitlist_form():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.text_input(key="waitlist_email").input("not-an-email")
    next(b for b in at.button if b.label == "Get the launch price").click().run()
    assert any("incomplete" in e.value for e in at.error)
    at.text_input(key="waitlist_email").input("student@uni.edu")
    next(b for b in at.button if b.label == "Get the launch price").click().run()
    assert any("on the list" in s.value for s in at.success)
    assert not at.exception
