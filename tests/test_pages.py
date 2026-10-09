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


# ---------- Review with checks, verifier and a stable score ----------

def open_analyze(at):
    """The review page in demo: consent and goal, without running the review."""
    at.run()
    at.switch_page("views/analyze.py").run()
    at.checkbox(key="consent_analyze").check().run()
    at.text_input(key="target_role").input("Business Analyst").run()
    return at


def review_demo():
    """Demo review up to the results; returns the AppTest after clicking "Review my CV"."""
    at = open_analyze(AppTest.from_file(APP, default_timeout=60))
    next(b for b in at.button if b.label == "Review my CV").click().run()
    assert not at.exception
    return at


def force_verification(monkeypatch, module, *, dropped: int, fixed: int) -> None:
    """The module's verifier (cvmax.analyze or cvmax.grill) reports the given numbers; the edits stay."""
    from cvmax.verify import Verification

    real = module.verify_edits

    def lossy(client, **kwargs):
        edits, verification = real(client, **kwargs)
        return edits, Verification(checked=verification.checked, fixed=fixed, dropped=dropped, notes=[])

    monkeypatch.setattr(module, "verify_edits", lossy)


def captions(at) -> list[str]:
    return [c.value for c in at.caption]


def test_review_shows_automatic_checks_and_score_caption():
    from cvmax.profile import PROGRAMS

    at = review_demo()
    run = at.session_state["analysis_run"]
    assert run.checks.findings and run.score.penalty > 0  # the demo CV has findings
    assert f"Automatic checks ({len(run.checks.findings)})" in [e.label for e in at.expander]
    program = PROGRAMS[at.session_state["profile"].program]
    assert f"Weighted rubric criteria for {program}; {run.score.penalty} points off for automatic checks" in captions(at)
    metric = next(m for m in at.metric if m.label == "CV fit for this goal")
    assert metric.value == f"{run.analysis.overall_score}/100" == f"{run.score.score}/100"
    assert at.session_state["analysis"] is run.analysis


def test_checks_expander_lists_findings_by_severity_with_icons():
    at = review_demo()
    run = at.session_state["analysis_run"]
    box = next(e for e in at.expander if e.label.startswith("Automatic checks"))
    bullets = [m.value for m in box.markdown if m.value.startswith("- :material/")]
    assert len(bullets) == len(run.checks.findings)
    icons = [b.split(" ", 2)[1] for b in bullets]
    order = {":material/error:": 0, ":material/warning:": 1, ":material/info:": 2}
    assert [order[i] for i in icons] == sorted(order[i] for i in icons)  # high first


def test_checks_expander_escapes_text_from_the_cv(monkeypatch):
    """A CV line in a finding cannot become a link or an image."""
    import cvmax.analyze as analyze_module
    from cvmax.checks import CheckReport, Finding

    evil = "[click](https://evil.example/x.png?d=1) **bold** <b>tag</b>"
    report = CheckReport(findings=[Finding("weak_opener", "medium", "Bullet <i>opens</i> badly.", evil)], metrics={})
    monkeypatch.setattr(analyze_module, "run_checks", lambda cv, profile: report)
    at = review_demo()
    box = next(e for e in at.expander if e.label == "Automatic checks (1)")
    body = next(m.value for m in box.markdown if m.value.startswith("- :material/"))
    assert "](" not in body.replace("\\]\\(", "")  # square and round brackets are escaped
    assert "<b>" not in body and "<i>" not in body and "**bold**" not in body
    assert "`https://evil.example/x.png?d=1" in body  # the address is shown as code, not as a link


def test_score_caption_without_penalties_and_without_checks(monkeypatch):
    import cvmax.analyze as analyze_module
    from cvmax import config
    from cvmax.checks import CheckReport
    from cvmax.profile import PROGRAMS

    monkeypatch.setattr(analyze_module, "run_checks", lambda cv, profile: CheckReport(findings=[], metrics={}))
    at = review_demo()
    program = PROGRAMS[at.session_state["profile"].program]
    assert f"Weighted rubric criteria for {program}; no automatic-check penalties" in captions(at)
    assert "Automatic checks (0)" in [e.label for e in at.expander]

    monkeypatch.setattr(config, "CHECKS_ENABLED", False)  # no checks: say nothing about penalties
    at = review_demo()
    assert at.session_state["analysis_run"].checks is None
    assert f"Weighted rubric criteria for {program}" in captions(at)
    assert not any(e.label.startswith("Automatic checks") for e in at.expander)


def test_score_caption_when_too_few_criteria_matched(monkeypatch):
    import cvmax.analyze as analyze_module
    from cvmax.scoring import ScoreDetail

    monkeypatch.setattr(
        analyze_module, "compute_score",
        lambda analysis, checks, program: ScoreDetail(
            score=40, criteria_score=58, model_score=58, penalty=3, matched=2, items=[]),
    )
    at = review_demo()
    assert "Overall assessment of the review; 3 points off for automatic checks" in captions(at)
    assert not any(c.startswith("Weighted rubric criteria") for c in captions(at))


@pytest.mark.parametrize("dropped, fixed, expected", [
    (1, 0, "1 suggested edit was removed because it added facts that are not in your CV."),
    (2, 0, "2 suggested edits were removed because they added facts that are not in your CV."),
    (0, 2, "2 suggested edits were trimmed because they added facts that are not in your CV."),
    (3, 1, "3 suggested edits were removed and 1 trimmed because they added facts that are not in your CV."),
])
def test_edits_tab_notes_what_the_verifier_removed(monkeypatch, dropped, fixed, expected):
    import cvmax.analyze as analyze_module

    force_verification(monkeypatch, analyze_module, dropped=dropped, fixed=fixed)
    at = review_demo()
    assert expected in captions(at)


def test_edits_tab_has_no_verifier_note_when_nothing_was_removed():
    at = review_demo()
    assert at.session_state["analysis_run"].verification.dropped == 0
    assert not any("suggested edit" in c for c in captions(at))


def test_analysis_done_event_has_score_and_verifier_fields(monkeypatch):
    from ui.account import get_db
    import cvmax.analyze as analyze_module

    force_verification(monkeypatch, analyze_module, dropped=1, fixed=2)
    at = review_demo()
    run = at.session_state["analysis_run"]
    aid = at.session_state["analysis_id"]
    done = [e for e in get_db().events if e["kind"] == "analysis_done" and e["payload"].get("analysis_id") == aid]
    assert len(done) == 1
    p = done[0]["payload"]
    assert p["penalty"] == run.score.penalty > 0
    assert p["model_score"] == run.score.model_score
    assert p["criteria_score"] == run.score.criteria_score
    assert p["n_checks"] == len(run.checks.findings) > 0
    assert (p["verify_dropped"], p["verify_fixed"]) == (1, 2)
    assert p["score"] == run.analysis.overall_score == run.score.score  # the log holds the final score, not the model's score


def test_saved_result_has_score_detail_as_plain_dict(monkeypatch):
    import json
    import ui.account

    saved = []
    monkeypatch.setattr(ui.account, "save_result", lambda kind, title, payload: saved.append((kind, title, payload)))
    at = review_demo()
    run = at.session_state["analysis_run"]
    (kind, title, payload), = saved
    assert kind == "analysis" and title.endswith(f"{run.score.score}/100")
    detail = payload["score_detail"]
    assert type(detail) is dict
    assert {k: detail[k] for k in ("score", "criteria_score", "model_score", "penalty", "matched")} == {
        "score": run.score.score, "criteria_score": run.score.criteria_score,
        "model_score": run.score.model_score, "penalty": run.score.penalty, "matched": run.score.matched}
    assert detail["items"] == [list(i) for i in run.score.items] and detail["items"]
    assert detail["score"] == payload["analysis"]["overall_score"]
    json.dumps(payload)  # payload without dataclasses and tuples: it can be stored in the database as is


def finish_qa(at) -> None:
    """Q&A in demo: start, one answer, "Finish and get edits"."""
    next(b for b in at.button if b.label == "Start Q&A").click().run()
    next(t for t in at.text_area if t.label == "Your answer").input("I organised 6 events for 300 students").run()
    next(b for b in at.button if b.label == "Answer").click().run()
    next(b for b in at.button if b.label == "Finish and get edits").click().run()
    assert not at.exception


def test_qa_finalize_adds_verified_edits_to_the_edits_tab():
    at = review_demo()
    before = len(at.session_state["analysis"].edits)
    finish_qa(at)
    result = at.session_state["grill_result"]
    assert result.edits and at.session_state["grill"].verification is not None
    assert at.session_state["grill"].verification.dropped == 0
    assert len([c for c in at.checkbox if str(c.key).startswith("accept_")]) == before + len(result.edits)
    assert any("Organised" in m.value for m in at.markdown)
    assert not any("suggested edit" in c for c in captions(at))


def test_qa_finalize_edits_removed_by_verifier_are_not_shown(monkeypatch):
    import cvmax.grill as grill_module
    from cvmax.verify import Verification

    monkeypatch.setattr(grill_module, "verify_edits", lambda client, **kw: ([], Verification(checked=1, dropped=1)))
    at = review_demo()
    before = len(at.session_state["analysis"].edits)
    finish_qa(at)
    assert at.session_state["grill_result"].edits == []
    assert len([c for c in at.checkbox if str(c.key).startswith("accept_")]) == before
    assert not any("Organised" in m.value for m in at.markdown)
    assert "1 suggested edit was removed because it added facts that are not in your CV." in captions(at)
