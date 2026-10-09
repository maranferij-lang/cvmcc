"""Every page opens in demo mode without errors."""

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


def test_analyze_jobs_tab_renders_in_demo():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.switch_page("views/analyze.py").run()
    at.checkbox(key="consent_analyze").check().run()
    at.text_input(key="target_role").input("Business Analyst").run()
    next(b for b in at.button if b.label == "Review my CV").click().run()
    next(b for b in at.button if b.label == "Show live vacancies").click().run()
    assert not at.exception
    assert any(str(k).startswith("jobs_analyze") for k in list(at.session_state))


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
    """The questionnaire in demo starts from the first question every time, and a model error does not leave an empty tab."""
    import cvmax.grill
    from cvmax.llm import LLMError

    def start(at):
        at.switch_page("views/analyze.py").run()
        at.checkbox(key="consent_analyze").check().run()
        at.text_input(key="target_role").input("Business Analyst").run()
        next(b for b in at.button if b.label == "Review my CV").click().run()
        next(b for b in at.button if b.label == "Start Q&A").click().run()

    for _ in range(2):  # the second time it used to give an empty tab
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
    assert any(b.label == "Start Q&A" for b in at.button)  # you can try again


def test_rating_a_review_sends_feedback():
    """The thumbs-up under the analysis used to crash: a local function shadowed send_feedback."""
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


def test_career_shows_live_vacancies_in_demo():
    from ui.account import get_db

    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.switch_page("views/career.py").run()
    at.checkbox(key="consent_career").check().run()
    next(b for b in at.button if b.label == "Find directions").click().run()
    next(b for b in at.button if b.label == "Show live vacancies").click().run()
    assert not at.exception
    from cvmax.jobs.providers import ALL
    from cvmax.jobs.providers.base import allowed_url

    domains = tuple(d for p in ALL for d in p.domains)
    state = next(at.session_state[k] for k in at.session_state.keys() if str(k).startswith("jobs_career-0"))
    ranked = state[1]  # (result, ranked, note)
    assert len(ranked) >= 6
    urls = [el.proto.url for el in at.get("link_button")]
    assert all(allowed_url(u, domains) for u in urls)  # every direct link is from an allowed domain
    shown = {v.url for v, _ in ranked}
    assert len(shown & set(urls)) >= 6  # the postings themselves, not only search links
    assert any(e["kind"] == "jobs_shown" for e in get_db().events)


def test_analyze_logs_events():
    from ui.account import get_db

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
    aid = at.session_state["analysis_id"]
    mine = [e for e in get_db().events if e["payload"].get("analysis_id") == aid]
    kinds = {e["kind"] for e in mine}
    assert {"analysis_done", "edits_decided"} <= kinds
    for e in mine:
        assert all(len(v) <= 200 for v in e["payload"].values() if isinstance(v, str))
