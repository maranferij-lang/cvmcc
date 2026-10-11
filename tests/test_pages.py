"""Every page opens in demo mode without errors."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")
PAGES = ["views/home.py", "views/analyze.py", "views/career.py", "views/learn.py", "views/builder.py",
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
    next(b for b in at.button if b.label == "Notify me").click().run()
    assert any("incomplete" in e.value for e in at.error)
    at.text_input(key="waitlist_email").input("student@uni.edu")
    next(b for b in at.button if b.label == "Notify me").click().run()
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


# ---------- Review against a posting (Match to this posting) ----------

POSTING = (
    "Junior Data Analyst. You are studying economics, statistics or a related field. You build reports in Excel "
    "and query tables with SQL. A team player. Dashboards in Tableau or Power BI are a plus. "
) * 2  # 300+ characters, as a pasted posting


def review_with_posting(text: str = POSTING):
    """Demo review with the posting text in the form; returns the AppTest after clicking "Review my CV"."""
    at = open_analyze(AppTest.from_file(APP, default_timeout=60))
    at.text_area(key="analyze_vacancy").input(text).run()
    next(b for b in at.button if b.label == "Review my CV").click().run()
    assert not at.exception
    return at


def match_expander(at):
    """The list of requirements of the match block, or None."""
    return next((e for e in at.expander if e.label.startswith("Requirements from the posting")), None)


def events_of(kind: str, analysis_id: str) -> list[dict]:
    from ui.account import get_db

    return [e for e in get_db().events if e["kind"] == kind and e["payload"].get("analysis_id") == analysis_id]


def test_review_with_a_posting_shows_the_match_block():
    from cvmax.match import match_score
    from cvmax.schemas import VacancyMatch

    assert len(POSTING) >= 300
    at = review_with_posting()
    box = match_expander(at)
    assert box is not None and box.label == "Requirements from the posting (6)"
    assert "Match to this posting" in [h.value for h in at.subheader]
    assert "**61% · 2 of 4 must-haves covered**" in [m.value for m in at.markdown]  # the demo posting match is always 61
    aid = at.session_state["analysis_id"]
    stored = at.session_state[f"match:{aid}"]
    assert isinstance(stored["match"], VacancyMatch) and match_score(stored["match"]) == 61
    lines = [m.value for m in box.markdown]
    assert len(lines) == 6
    assert lines[0].startswith("- :material/check_circle: **Covered** · :red-badge[Must]")
    assert "On your CV: «Responsible for making reports in Excel»" in lines[1]  # the quote is shown
    assert lines[2].startswith("- :material/contrast: **Partial**") and "**To do:**" in lines[2]
    assert lines[3].startswith("- :material/cancel: **Missing**") and "On your CV" not in lines[3]
    assert ":gray-badge[Nice to have]" in lines[4]
    assert "Posting: Junior Data Analyst" in captions(at)
    assert any(c.startswith("Words from the posting that are not on your CV: SQL, Tableau") for c in captions(at))
    assert not any(c.startswith("Tip: paste a job posting") for c in captions(at))


def test_match_done_event_has_counts_and_source():
    at = review_with_posting()
    aid = at.session_state["analysis_id"]
    done = events_of("match_done", aid)
    assert len(done) == 1
    assert done[0]["payload"] == {"analysis_id": aid, "score": 61, "must_total": 4, "must_covered": 2,
                                  "nice_total": 2, "source": "form"}  # counts only: no posting and no CV text


@pytest.mark.parametrize("text, shown", [("", False), ("p" * 199, False), ("p" * 200, True)])
def test_match_block_needs_a_posting_of_200_characters(text, shown):
    at = review_with_posting(text) if text else review_demo()
    aid = at.session_state["analysis_id"]
    assert (match_expander(at) is not None) is shown
    assert (f"match:{aid}" in at.session_state) is shown
    assert len(events_of("match_done", aid)) == int(shown)
    assert any(c.startswith("Tip: paste a job posting") for c in captions(at)) is (not shown)
    assert any(m.label == "CV fit for this goal" for m in at.metric)  # the rest of the page is the same


def test_match_runs_once_per_analysis_and_is_replaced_by_the_next_review(monkeypatch):
    import cvmax.match

    real, calls = cvmax.match.match_vacancy, []

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(cvmax.match, "match_vacancy", counting)
    at = review_with_posting()
    first = at.session_state["analysis_id"]
    for box in at.checkbox:  # reruns of the page (ticking edits) do not call the model again
        if str(box.key).startswith("accept_"):
            box.check()
    at.run()
    assert len(calls) == 1 and len(events_of("match_done", first)) == 1
    next(b for b in at.button if b.label == "Review my CV").click().run()  # a new review: a new match
    second = at.session_state["analysis_id"]
    assert second != first and len(calls) == 2
    assert f"match:{second}" in at.session_state and f"match:{first}" not in at.session_state
    assert len(events_of("match_done", second)) == 1


def test_match_text_from_the_model_is_escaped(monkeypatch):
    """The posting and the model can put a link, an image or markup into any field."""
    import cvmax.match
    from cvmax.schemas import Requirement, VacancyMatch

    evil = "[click](https://evil.example/x.png?d=1) **bold** <b>tag</b>"
    result = VacancyMatch(
        role_title="<b>Boss</b> ![x](https://evil.example/r.png)",
        requirements=[
            Requirement(text=evil, kind="must", status="partial", evidence=evil, action=evil),
            Requirement(text="Python", kind="must", status="missing", evidence="", action=""),
        ],
        keywords_missing=["<script>alert(1)</script>", "[k](https://evil.example/k)"],
        summary="![img](https://evil.example/p.png?d=1) **summary**",
    )
    monkeypatch.setattr(cvmax.match, "match_vacancy", lambda *args, **kwargs: result)
    at = review_with_posting()
    box = match_expander(at)
    assert box is not None
    shown = [m.value for m in box.markdown] + [m.value for m in at.markdown if "summary" in m.value]
    shown += captions(at)
    for text in shown:
        flat = text.replace("\\]\\(", "")
        assert "](" not in flat and "<b>" not in text and "<script>" not in text and "**bold**" not in text
        assert "![" not in text.replace("\\!\\[", "")
    # A missing requirement whose action was cleared by the verifier still says what is going on.
    assert "**To do:** Not found on your CV." in box.markdown[1].value
    assert "`https://evil.example/x.png?d=1" in box.markdown[0].value  # an address is shown as code, not as a link


def test_match_model_error_is_shown_softly_kept_and_can_be_retried(monkeypatch):
    import cvmax.match
    from cvmax.llm import LLMError

    real, state = cvmax.match.match_vacancy, {"calls": 0, "fail": True}

    def flaky(*args, **kwargs):
        state["calls"] += 1
        if state["fail"]:
            raise LLMError("The free AI models are overloaded right now. Try again in a minute.")
        return real(*args, **kwargs)

    monkeypatch.setattr(cvmax.match, "match_vacancy", flaky)
    at = review_with_posting()
    assert any("overloaded" in i.value for i in at.info)  # st.info, not st.error: the review itself worked
    assert not at.error and match_expander(at) is None
    assert any(m.label == "CV fit for this goal" for m in at.metric)
    assert events_of("match_done", at.session_state["analysis_id"]) == []
    at.run()  # the failure is kept: a rerun does not call the model again
    assert state["calls"] == 1 and any("overloaded" in i.value for i in at.info)

    state["fail"] = False
    next(b for b in at.button if b.label == "Try again").click().run()
    assert not at.exception and state["calls"] == 2
    assert match_expander(at) is not None and not any("overloaded" in i.value for i in at.info)
    assert len(events_of("match_done", at.session_state["analysis_id"])) == 1


def test_match_when_the_daily_limit_is_used_up(monkeypatch):
    import cvmax.match
    from cvmax import config

    calls = []
    monkeypatch.setattr(cvmax.match, "match_vacancy", lambda *args, **kwargs: calls.append(1))
    monkeypatch.setitem(config.LIMITS, "match", (0, 60))
    at = review_with_posting()
    assert not calls and match_expander(at) is None
    assert any("You have used all 0 posting matches for today" in w.value for w in at.warning)  # take_limit explains
    assert "Your CV review is complete; only the match to the posting is paused." in captions(at)
    assert any(m.label == "CV fit for this goal" for m in at.metric)
    assert events_of("match_done", at.session_state["analysis_id"]) == []


def test_match_for_a_cv_without_text_does_not_spend_a_use(monkeypatch):
    import cvmax.match
    from cvmax.cv_input import CVFile

    calls = []
    monkeypatch.setattr(cvmax.match, "match_vacancy", lambda *args, **kwargs: calls.append(1))
    at = open_analyze(AppTest.from_file(APP, default_timeout=60))
    at.session_state["cv_file"] = CVFile(filename="scan.pdf", text="", pdf_bytes=b"%PDF-1.4")
    at.text_area(key="analyze_vacancy").input(POSTING).run()
    next(b for b in at.button if b.label == "Review my CV").click().run()
    assert not at.exception and not calls
    assert any("looks like a scan" in i.value for i in at.info)


# ---------- Jobs tab: "Review for this posting" ----------

LONG_POSTING = (
    "Junior Data Analyst at Northwind. Requirements: you study economics or statistics, you build Excel reports and "
    "you query tables with SQL. Nice to have: Tableau. You work in a small friendly team and learn fast. "
) * 3  # 600+ characters
REVIEW_LABEL = "Review for this posting"


def show_postings(monkeypatch, at):
    """Shows the demo postings of the Jobs tab. The first one has a long description, the second a short one.

    Returns (long_id, short_id): the ids that the review buttons are keyed with.
    """
    import dataclasses

    import ui.jobs

    real = ui.jobs.demo_search_result

    def with_descriptions(q, **kwargs):
        result = real(q, **kwargs)
        texts = [LONG_POSTING, "Too short to hold the requirements."] + [""] * (len(result.vacancies) - 2)
        vacancies = [dataclasses.replace(v, description=t) for v, t in zip(result.vacancies, texts)]
        return dataclasses.replace(result, vacancies=vacancies)

    monkeypatch.setattr(ui.jobs, "demo_search_result", with_descriptions)
    next(b for b in at.button if b.label == "Show live vacancies").click().run()
    assert not at.exception
    state = next(at.session_state[k] for k in at.session_state.keys() if str(k).startswith("jobs_analyze"))
    by_description = {v.description: v.id for v, _ in state[1]}
    return by_description[LONG_POSTING], by_description["Too short to hold the requirements."]


def test_jobs_tab_has_a_review_button_on_every_posting(monkeypatch):
    at = review_demo()
    show_postings(monkeypatch, at)
    state = next(at.session_state[k] for k in at.session_state.keys() if str(k).startswith("jobs_analyze"))
    assert len([b for b in at.button if b.label == REVIEW_LABEL]) == len(state[1]) >= 6


def test_jobs_review_button_puts_a_long_posting_into_the_form(monkeypatch):
    at = review_demo()
    long_id, _ = show_postings(monkeypatch, at)
    assert at.text_area(key="analyze_vacancy").value == ""
    at.button(key=f"review_analyze_{long_id}").click().run()
    assert not at.exception
    assert at.text_area(key="analyze_vacancy").value == LONG_POSTING.strip()  # the form field now holds the posting
    assert at.session_state["open_form"] is True  # the form is open, so the user sees it
    assert "prefill_vacancy" not in at.session_state
    assert any(t.value == "Posting pasted into the form. Click Review my CV." for t in at.toast)

    # The posting is in the form: reviewing now matches the CV to it, and the event says where it came from.
    next(b for b in at.button if b.label == "Review my CV").click().run()
    assert not at.exception
    assert match_expander(at) is not None
    done = events_of("match_done", at.session_state["analysis_id"])
    assert [e["payload"]["source"] for e in done] == ["jobs"]
    assert "open_form" not in at.session_state


def test_posting_edited_after_pasting_counts_as_the_form(monkeypatch):
    at = review_demo()
    long_id, _ = show_postings(monkeypatch, at)
    at.button(key=f"review_analyze_{long_id}").click().run()
    at.text_area(key="analyze_vacancy").input(LONG_POSTING + " I changed this text.").run()
    next(b for b in at.button if b.label == "Review my CV").click().run()
    done = events_of("match_done", at.session_state["analysis_id"])
    assert [e["payload"]["source"] for e in done] == ["form"]


def test_jobs_review_button_with_a_short_description_asks_to_paste(monkeypatch):
    at = review_demo()
    _, short_id = show_postings(monkeypatch, at)
    at.button(key=f"review_analyze_{short_id}").click().run()
    assert not at.exception
    assert any(i.value == "Open the posting and paste its text into the form above." for i in at.info)
    assert at.text_area(key="analyze_vacancy").value == ""  # nothing was pasted
    assert "prefill_vacancy" not in at.session_state and "open_form" not in at.session_state
    assert not any("Posting pasted" in t.value for t in at.toast)


def test_career_page_has_no_review_buttons():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.switch_page("views/career.py").run()
    at.checkbox(key="consent_career").check().run()
    next(b for b in at.button if b.label == "Find directions").click().run()
    next(b for b in at.button if b.label == "Show live vacancies").click().run()
    assert not at.exception
    assert any(str(k).startswith("jobs_career-0") for k in at.session_state.keys())
    assert not any(b.label == REVIEW_LABEL for b in at.button)  # on_review is optional: no callback, no button


# ---------- Share card ----------

def card_size(png: bytes) -> tuple[int, int]:
    import io

    from PIL import Image

    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    return Image.open(io.BytesIO(png)).size


def review_again(at, role: str | None = None) -> str:
    """Another review in the same session (optionally for another role); returns the new analysis id."""
    old = at.session_state["analysis_id"]
    if role is not None:
        at.text_input(key="target_role").input(role).run()
    next(b for b in at.button if b.label == "Review my CV").click().run()
    assert not at.exception
    assert at.session_state["analysis_id"] != old
    return at.session_state["analysis_id"]


def test_share_row_has_two_download_buttons_with_png_cards():
    from cvmax.share_card import CARD_SIZES

    at = review_demo()
    labels = [el.proto.label for el in at.get("download_button")]
    assert "Download card (1080×1350)" in labels and "Download square (1080×1080)" in labels
    assert "**Share your score**" in [m.value for m in at.markdown]
    assert not any(m.value.startswith("**Your progress") for m in at.markdown)  # a first review has no progress
    cards = at.session_state[f"share:{at.session_state['analysis_id']}"]
    assert {size: card_size(png) for size, png in cards.items()} == {k: CARD_SIZES[k] for k in ("story", "square")}
    assert "The card shows only the score and your target role: no name, no CV text." in captions(at)


def test_download_button_logs_the_share_card_event():
    at = review_demo()
    aid = at.session_state["analysis_id"]
    score = at.session_state["analysis"].overall_score
    assert events_of("share_card", aid) == []  # drawing the card is not an event; downloading it is
    at.download_button(key="share_square").click().run()
    assert not at.exception
    (event,) = events_of("share_card", aid)
    assert event["payload"] == {"analysis_id": aid, "before": None, "after": score, "size": "square"}
    at.download_button(key="share_story").click().run()
    assert [e["payload"]["size"] for e in events_of("share_card", aid)] == ["square", "story"]


def shown(at) -> list[str]:
    return [m.value for m in at.markdown]


def has_progress(at) -> bool:
    return any(v.startswith("**Your progress") for v in shown(at))


def edited_cv(at, extra: str = "Led a team of 4 students to ship a dashboard, cutting reporting time by 30%.") -> None:
    """Replaces the CV of the session with the demo CV plus one more line (a changed CV text)."""
    from cvmax.cv_input import CVFile
    from cvmax.demo import DEMO_CV_TEXT

    at.session_state["cv_file"] = CVFile(filename="cv_v2.txt", text=f"{DEMO_CV_TEXT}\n- {extra}")


def previous_score_was(at, score: int) -> None:
    """Pretends the review before this one scored `score` (the demo model always gives the same score)."""
    at.session_state["last_analysis"] = {**at.session_state["last_analysis"], "score": score}


def test_rerun_of_the_same_cv_and_role_is_not_progress():
    at = review_demo()
    first = at.session_state["analysis_id"]
    score = at.session_state["analysis"].overall_score

    second = review_again(at)  # nothing changed: same CV, same role, same score
    assert at.session_state["analysis"].overall_score == score
    assert "**Share your score**" in shown(at) and not has_progress(at)
    assert f"share:{first}" not in at.session_state
    at.download_button(key="share_story").click().run()
    (event,) = events_of("share_card", second)
    assert event["payload"] == {"analysis_id": second, "before": None, "after": score, "size": "story"}
    # Running it a third time does not turn into progress either.
    review_again(at)
    assert "**Share your score**" in shown(at) and not has_progress(at)


def test_improved_score_with_a_changed_cv_shows_progress():
    at = review_demo()
    first = at.session_state["analysis_id"]
    single = at.session_state[f"share:{first}"]["story"]
    score = at.session_state["analysis"].overall_score
    previous_score_was(at, score - 10)  # the first review scored lower than this one will

    edited_cv(at)
    second = review_again(at)
    assert f"**Your progress: {score - 10} → {score}**" in shown(at)
    assert "**Share your score**" not in shown(at)
    progress = at.session_state[f"share:{second}"]["story"]
    assert card_size(progress) == card_size(single) and progress != single  # the before/after card
    at.download_button(key="share_story").click().run()
    (event,) = events_of("share_card", second)
    assert event["payload"] == {"analysis_id": second, "before": score - 10, "after": score, "size": "story"}


def test_lower_score_after_a_changed_cv_is_not_progress():
    at = review_demo()
    score = at.session_state["analysis"].overall_score
    previous_score_was(at, score + 10)  # the previous review scored higher
    edited_cv(at)
    review_again(at)
    assert "**Share your score**" in shown(at) and not has_progress(at)


def test_a_different_role_is_not_progress_even_with_a_changed_cv():
    at = review_demo()
    previous_score_was(at, at.session_state["analysis"].overall_score - 10)
    edited_cv(at)
    review_again(at, "Data Analyst")
    assert "**Share your score**" in shown(at) and not has_progress(at)


def test_progress_ignores_letter_case_and_spaces_in_the_role():
    at = review_demo()
    previous_score_was(at, at.session_state["analysis"].overall_score - 10)
    edited_cv(at)
    review_again(at, "  business   ANALYST ")
    assert any(m.value.startswith("**Your progress: ") for m in at.markdown)
    assert len(events_of("rescan", at.session_state["analysis_id"])) == 1  # the same goal for the learning events too


def test_a_new_goal_or_a_lower_score_is_not_progress():
    at = review_demo()
    review_again(at, "Data Analyst")  # another role: not a rescan
    assert "**Share your score**" in [m.value for m in at.markdown]
    assert not any(m.value.startswith("**Your progress") for m in at.markdown)

    # Same role, but the previous review scored higher: sharing "99 -> 58" as progress would be wrong.
    last = dict(at.session_state["last_analysis"])
    at.session_state["last_analysis"] = {**last, "score": 99}
    review_again(at)
    assert "**Share your score**" in [m.value for m in at.markdown]
    assert not any(m.value.startswith("**Your progress") for m in at.markdown)


def test_a_failing_card_does_not_break_the_results_page(monkeypatch):
    import cvmax.share_card

    def broken(*args, **kwargs):
        raise OSError("font file is gone")

    monkeypatch.setattr(cvmax.share_card, "render_card", broken)
    at = review_demo()
    assert not any(el.proto.label.startswith("Download card") for el in at.get("download_button"))
    assert any(m.label == "CV fit for this goal" for m in at.metric)


def test_every_daily_limit_has_a_readable_name():
    from cvmax import config
    from ui.account import LIMIT_NAMES

    assert set(config.LIMITS) <= set(LIMIT_NAMES)  # otherwise the message says "all 5 requests"
    assert LIMIT_NAMES["match"] == "posting matches"
