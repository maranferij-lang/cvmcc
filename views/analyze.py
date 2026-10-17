"""The "CV review" page: overview, experience questionnaire, edits, learning plan, ready CV."""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import asdict

import streamlit as st

from cvmax import config
from cvmax.analyze import AnalysisRun, analyze_full
from cvmax.cv_render import has_placeholders, pdf_preview, render_docx, render_pdf
from cvmax.learning.bandit import choose_variant, variant_text
from cvmax.learning.version import knowledge_version, lessons_version
from cvmax.edits import apply_edits, changed_action, changes_markdown, lost_facts, unverified_terms
from cvmax.grill import GrillSession, answer, finalize, next_question
from cvmax.llm import LLMError
from cvmax.match import match_counts, match_score, match_vacancy
from cvmax.profile import ANY_COMPANY, COMPANY_TYPES, FEEDBACK_LANGUAGES, LEVELS, PROGRAMS, REGIONS, STATUSES, Profile
from cvmax.resources import CatalogError, match_skills
from cvmax.safe_text import md_escape
from cvmax.schemas import Requirement, VacancyMatch
from cvmax.scoring import MIN_MATCHED
from cvmax.share_card import CARD_SIZES, render_card
from cvmax.structure import changed_bullets, structure_cv
from ui.account import (
    is_logged_in, limit_caption, log_edit_feedback, log_event, profile_value, require_login, save_improvement_sample,
    save_result, send_feedback,
    skill_demand_cached, take_limit, variant_stats_cached,
)
from ui import billing
from ui.common import BOT_AVATAR, card, consent, cv_picker, demo_banner, get_llm
from ui.jobs import render_jobs
from ui.resources import render_controls, render_how_we_pick, render_resources, SOFT_ERROR

log = logging.getLogger("cvmax")


def plural(n: int, word: str) -> str:
    """A number with an English word: 1 line, 5 lines."""
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


# Review against a posting: shorter posting text is not worth a model call, and the form holds at most this much.
MATCH_MIN_CHARS = 200
VACANCY_MAX_CHARS = 8000
# The Jobs tab puts a posting into the form only when its description is long enough to hold the requirements.
REVIEW_MIN_CHARS = 300

# Share cards offered for download: size name -> label and file name.
SHARE_DOWNLOADS = {
    "story": ("Download card", "getcvmax-score.png"),
    "square": ("Download square", "getcvmax-score-square.png"),
}

PRIORITY_LABEL = {"high": ":red-badge[High]", "medium": ":gray-badge[Medium]", "low": ":gray-badge[Low]"}

# Icon by severity of an automatic-check finding; the most important are shown first.
CHECK_ICON = {"high": ":material/error:", "medium": ":material/warning:", "low": ":material/info:"}
CHECK_ORDER = {"high": 0, "medium": 1, "low": 2}

# Status of a posting requirement: an icon and a word (the word is for screen readers and for black-and-white print).
MATCH_ICON = {"covered": ":material/check_circle:", "partial": ":material/contrast:", "missing": ":material/cancel:"}
MATCH_WORD = {"covered": "Covered", "partial": "Partial", "missing": "Missing"}
MATCH_KIND = {"must": ":red-badge[Must]", "nice": ":gray-badge[Nice to have]"}


IMPACT_RANK = {"high": 0, "medium": 1, "low": 2}


def gap_demand(skills: list[str], demand: list[dict]) -> int:
    """The highest number of postings among the market skills that the gap maps to (0 when none is listed)."""
    by_skill = {str(d.get("skill", "")).lower(): int(d.get("postings") or 0) for d in demand}
    return max((by_skill.get(sid.lower(), 0) for sid in skills), default=0)


def order_gaps(gaps: list, skills: list[list[str]], demand: list[dict]) -> list[int]:
    """Gap indexes in display order. With market demand data, gaps whose skills are asked for more come first and
    the impact order breaks ties; without demand data the order of the analysis is kept."""
    order = list(range(len(gaps)))
    if demand:
        order.sort(key=lambda i: (-gap_demand(skills[i], demand), IMPACT_RANK.get(gaps[i].impact, 3), i))
    return order


def get_client():
    return get_llm()


def score_caption(run: AnalysisRun | None, program: str) -> str:
    """Caption under the score: what it is made of. Empty if there is no review result."""
    if run is None:
        return ""
    score = run.score
    if score.matched >= MIN_MATCHED:
        base = f"Weighted rubric criteria for {md_escape(PROGRAMS.get(program, program))}"
    else:  # too few criteria for a weighted sum: the score is the overall assessment of the review
        base = "Overall assessment of the review"
    if run.checks is None:  # checks are off or failed: say nothing about penalties
        return base
    if score.penalty > 0:
        return f"{base}; {plural(score.penalty, 'point')} off for automatic checks"
    return f"{base}; no automatic-check penalties"


def verification_totals(s) -> tuple[int, int]:
    """How many edits the verifier removed and how many it trimmed: in the review and after the Q&A together."""
    run = s.get("analysis_run")
    found = [run.verification if run is not None else None]
    if s.grill is not None and s.grill_result is not None:
        found.append(s.grill.verification)
    done = [v for v in found if v is not None]
    return sum(v.dropped for v in done), sum(v.fixed for v in done)


def verification_caption(dropped: int, fixed: int) -> str:
    """Caption on the Edits tab about edits the verifier removed or trimmed. Empty if there were none."""
    if dropped <= 0 and fixed <= 0:
        return ""
    if dropped > 0 and fixed > 0:
        what = f"{plural(dropped, 'suggested edit')} {'was' if dropped == 1 else 'were'} removed and {fixed} trimmed"
    elif dropped > 0:
        what = f"{plural(dropped, 'suggested edit')} {'was' if dropped == 1 else 'were'} removed"
    else:
        what = f"{plural(fixed, 'suggested edit')} {'was' if fixed == 1 else 'were'} trimmed"
    return f"{what} because {'it' if dropped + fixed == 1 else 'they'} added facts that are not in your CV."


def render_checks(report) -> None:
    """Findings of the automatic checks: text from the CV and from the checks is shown only through md_escape."""
    findings = sorted(report.findings, key=lambda f: CHECK_ORDER.get(f.severity, len(CHECK_ORDER)))
    # No icon=: AppTest reports an expander with an icon as a status, not as an expander.
    with st.expander(f"Automatic checks ({len(findings)})"):
        if not findings:
            st.markdown("No problems found by the automatic checks.")
            return
        st.caption(
            "Counted by code, not by the AI. High findings take 4 points off the score, medium 2, low 1 "
            f"(at most {config.PENALTY_CAP} in total)."
        )
        for f in findings:
            icon = CHECK_ICON.get(f.severity, CHECK_ICON["low"])
            evidence = f"  \n  {md_escape(f.line)}" if f.line else ""
            st.markdown(f"- {icon} {md_escape(f.message)}{evidence}")


def same_text(a: str, b: str) -> bool:
    """The same text up to whitespace."""
    return " ".join((a or "").split()) == " ".join((b or "").split())


def same_role(a: str, b: str) -> bool:
    """The same target role: letter case and extra whitespace do not make a new goal."""
    return " ".join((a or "").casefold().split()) == " ".join((b or "").casefold().split())


def match_headline(m: VacancyMatch) -> str:
    """The score line of the match block: "68% · 5 of 7 must-haves covered"."""
    counts = match_counts(m)
    if counts["must_total"]:
        total, covered, word = counts["must_total"], counts["must_covered"], "must-have"
    else:  # a posting with only nice-to-haves
        total, covered, word = counts["nice_total"], counts["nice_covered"], "nice-to-have"
    return f"{match_score(m)}% · {covered} of {total} {word if total == 1 else word + 's'} covered"


def requirement_markdown(r: Requirement) -> str:
    """One requirement for the list: status, the posting's words, the CV quote and what to do.

    Everything except the fixed labels comes from the posting or the model, so it goes through md_escape.
    """
    lines = [f"- {MATCH_ICON[r.status]} **{MATCH_WORD[r.status]}** · {MATCH_KIND[r.kind]} {md_escape(r.text)}"]
    if r.evidence:
        lines.append(f"  \n  On your CV: «{md_escape(r.evidence)}»")
    if r.status != "covered":
        lines.append(f"  \n  **To do:** {md_escape(r.action) or 'Not found on your CV.'}")
    return "".join(lines)


def state():
    s = st.session_state
    s.setdefault("analysis", None)
    s.setdefault("analysis_run", None)
    s.setdefault("cv", None)
    s.setdefault("profile", None)
    s.setdefault("grill", None)
    s.setdefault("grill_result", None)
    s.setdefault("analysis_id", "")
    return s


def all_edits(s):
    edits = list(s.analysis.edits) if s.analysis else []
    if s.grill_result:
        edits += list(s.grill_result.edits)
    return edits


def latest_per_line(edits: list) -> list:
    """If two accepted edits change the same line, we keep the later one: the questionnaire knows more facts."""
    by_line: dict[str, object] = {}
    for e in edits:
        key = " ".join(e.before.lower().split()) or f"new:{id(e)}"
        by_line.pop(key, None)
        by_line[key] = e
    return list(by_line.values())


def edit_sources(s) -> list[str]:
    n_analysis = len(s.analysis.edits) if s.analysis else 0
    n_grill = len(s.grill_result.edits) if s.grill_result else 0
    return ["analysis"] * n_analysis + ["grill"] * n_grill


def log_accepted_edits(s) -> None:
    """Once per analysis (and again if the selection changed): which edits were accepted and which not."""
    edits = all_edits(s)
    items = [
        {"source": src, "section": e.section, "priority": e.priority, "before": e.before, "after": e.after,
         "accepted": bool(st.session_state.get(f"accept_{i}"))}
        for i, (e, src) in enumerate(zip(edits, edit_sources(s)))
    ]
    signature = tuple(x["accepted"] for x in items)
    if s.get("feedback_sent") == signature:
        return
    s["feedback_sent"] = signature
    log_edit_feedback(s.analysis_id, s.profile.target_role, s.profile.program, items)
    log_event("edits_decided", analysis_id=s.analysis_id, accepted=sum(x["accepted"] for x in items), total=len(items))


def log_export(s, fmt: str) -> None:
    """Selecting edits and the export event in one call for on_click."""
    log_accepted_edits(s)
    log_event("export", analysis_id=s.analysis_id, format=fmt)


def log_analysis(s, run: AnalysisRun, *, program: str, region: str, level: str, clarity: str, variant: str) -> None:
    """Events after a successful review: the result, and on a repeated review of the same goal also the dynamics."""
    a, role = s.analysis, s.profile.target_role
    previous = s.get("last_analysis")
    same = bool(previous) and same_role(previous["role"], role)
    verification = run.verification
    log_event("analysis_done", analysis_id=s.analysis_id, task="analysis", variant=variant, program=program,
              region=region, level=level, score=a.overall_score, knowledge_version=knowledge_version(),
              lessons_version=lessons_version(),
              previous_score=previous["score"] if same else None, n_edits=len(a.edits), n_gaps=len(a.gaps),
              clarity=clarity,
              # Score components, the number of automatic findings and what the edit verifier did.
              model_score=run.score.model_score, criteria_score=run.score.criteria_score,
              penalty=run.score.penalty, n_checks=len(run.checks.findings) if run.checks is not None else 0,
              verify_dropped=verification.dropped if verification is not None else 0,
              verify_fixed=verification.fixed if verification is not None else 0)
    if same:
        log_event("rescan", analysis_id=s.analysis_id, previous_analysis_id=previous["analysis_id"],
                  previous_score=previous["score"], score=a.overall_score,
                  delta=a.overall_score - previous["score"])
    # previous_score: the score to show as "before" on the share card. It is set only for the same goal, a CV whose text
    # changed since that review and a strictly higher score; a re-run of the same CV is not progress.
    cv_hash = hashlib.sha256((s.cv.text or "").strip().encode("utf-8")).hexdigest()
    improved = same and previous.get("cv_hash") != cv_hash and a.overall_score > previous["score"]
    s["last_analysis"] = {"analysis_id": s.analysis_id, "role": role, "score": a.overall_score, "cv_hash": cv_hash,
                          "previous_score": previous["score"] if improved else None}


def vacancy_match(s) -> dict | None:
    """The review against the posting of this analysis: made once and kept in session state (match:<analysis_id>).

    Returns {"match": VacancyMatch} or, when the model failed, {"match": None, "note": text}; both are kept, so a page
    rerun never calls the model again. None when the daily limit is used up (take_limit has shown why; not kept).
    """
    key = f"match:{s.analysis_id}"
    if key in s:
        return s[key]
    if not (s.cv.text or "").strip():  # a scanned PDF: nothing to quote, so do not spend a use
        s[key] = {"match": None,
                  "note": "Matching to a posting needs a CV with selectable text, and this PDF looks like a scan."}
        return s[key]
    if not take_limit("match"):
        return None
    try:
        with st.spinner("Comparing your CV with the posting..."):
            m = match_vacancy(get_client(), s.profile, s.cv, s.profile.vacancy_text)
    except LLMError as e:
        s[key] = {"match": None, "note": str(e)}
        return s[key]
    s[key] = {"match": m}
    log_event("match_done", analysis_id=s.analysis_id, score=match_score(m), source=s.get("match_source", "form"),
              **match_counts(m))
    return s[key]


def render_match(s) -> None:
    """The "Match to this posting" block of the Overview tab. Posting and model text is shown only through md_escape."""
    with card("match"):
        st.subheader("Match to this posting")
        result = vacancy_match(s)
        if result is None:
            st.caption("Your CV review is complete; only the match to the posting is paused.")
            return
        m = result["match"]
        if m is None:
            st.info(md_escape(result["note"]))
            if st.button("Try again", key=f"match_retry_{s.analysis_id}"):
                del s[f"match:{s.analysis_id}"]
                st.rerun()
            return
        if not m.requirements:
            st.info("No concrete requirements were found in the pasted text. Check that it is the whole posting, "
                    "with its requirements section.")
            return
        if m.role_title:
            st.caption(f"Posting: {md_escape(m.role_title)}")
        st.markdown(f"**{match_headline(m)}**")
        if m.summary:
            st.write(md_escape(m.summary))
        with st.expander(f"Requirements from the posting ({len(m.requirements)})"):
            for r in m.requirements:
                st.markdown(requirement_markdown(r))
        if m.keywords_missing:
            st.caption("Words from the posting that are not on your CV: "
                       + ", ".join(md_escape(k) for k in m.keywords_missing)
                       + ". Add one only where it is true for you.")
        st.caption("Counted by code from the statuses: a must-have weighs 1, a nice-to-have 0.3, a partial match half. "
                   "A quote that is not on your CV does not count.")


def review_for_posting(vacancy) -> None:
    """The "Review for this posting" button of the Jobs tab: puts the posting text into the form.

    The form's text area already exists in this script run, so its state cannot be set here; the text waits in
    prefill_vacancy and the top of the page puts it into the form (the same way as prefill_role).
    """
    text = (vacancy.description or "").strip()
    if len(text) < REVIEW_MIN_CHARS:
        st.info("Open the posting and paste its text into the form above.")
        return
    st.session_state["prefill_vacancy"] = text[:VACANCY_MAX_CHARS]
    st.session_state["open_form"] = True
    st.rerun()


def log_share(s, before: int | None, after: int, size: str) -> None:
    """on_click of a share-card download."""
    log_event("share_card", analysis_id=s.analysis_id, before=before, after=after, size=size)


def share_cards(s, before: int | None) -> dict[str, bytes]:
    """The share cards of this analysis, drawn once and kept in session state (share:<analysis_id>)."""
    key = f"share:{s.analysis_id}"
    if key not in s:
        s[key] = {size: render_card(s.analysis.overall_score, before=before, role=s.profile.target_role, size=size)
                  for size in SHARE_DOWNLOADS}
    return s[key]


def render_share(s) -> None:
    """The "Share your score" row under the score; after a repeated review of the same goal, "Your progress"."""
    after = s.analysis.overall_score
    last = s.get("last_analysis") or {}
    before = last.get("previous_score") if last.get("analysis_id") == s.analysis_id else None
    if before is not None and after <= before:
        before = None  # an equal or lower score is not progress, so the card shows the score alone
    try:
        cards = share_cards(s, before)
    except Exception as e:  # a card must not break the results page
        log.warning("share card failed: %s", e)
        return
    with card("share"):
        st.markdown(f"**Your progress: {before} → {after}**" if before is not None else "**Share your score**")
        for col, (size, (label, file_name)) in zip(st.columns(len(SHARE_DOWNLOADS)), SHARE_DOWNLOADS.items()):
            width, height = CARD_SIZES[size]
            col.download_button(f"{label} ({width}×{height})", cards[size], file_name, mime="image/png",
                                key=f"share_{size}", on_click=log_share, args=(s, before, after, size))
        st.caption("The card shows only the score and your target role: no name, no CV text.")


def mark_full(s) -> None:
    """The review is unlocked: remember the tier and save the full tier with the saved result."""
    s["tier"] = "full"
    payload = s.get("result_payload")
    if payload:
        save_result("analysis", f"{payload['role']} · {s.analysis.overall_score}/100 · full fix",
                    {**payload, "tier": "full"})


# ---------- Header ----------
require_login("CV review")
s = state()
# Values from the profile are put into the form once per session.
if not s.get("analyze_defaults_set"):
    s["analyze_defaults_set"] = True
    if profile_value("program") in PROGRAMS:
        s["analyze_program"] = profile_value("program")
    if profile_value("status") in STATUSES:
        s["analyze_status"] = profile_value("status")
    s["analyze_background"] = profile_value("background", "")
# Transition from the "Where to apply" page: we put the chosen direction into the form.
if "prefill_role" in st.session_state:
    st.session_state["target_role"] = st.session_state.pop("prefill_role")
    st.session_state["company_type"] = st.session_state.pop("prefill_company")
    s["open_form"] = True  # a new goal: show the form even if there is an old result
# Transition from the Jobs tab ("Review for this posting"): the posting text goes into the form's text area.
# This has to happen before the text area is created in this run.
if "prefill_vacancy" in st.session_state:
    st.session_state["analyze_vacancy"] = st.session_state.pop("prefill_vacancy")
    s["jobs_pasted"] = st.session_state["analyze_vacancy"]  # to tell later whether the posting came from the Jobs tab
    s["open_form"] = True
    st.toast("Posting pasted into the form. Click Review my CV.", icon=":material/content_paste:")

st.title("CV review")
st.caption("Edits tailored to a specific role, a short Q&A about your experience, and a plan for what to learn.")
demo_banner()
if not consent("analyze"):
    st.stop()

# ---------- Onboarding ----------
# After the analysis the form collapses, so the result is right under the heading.
form_box = (
    st.expander("Goal and CV: edit and run again", icon=":material/tune:", expanded=s.get("open_form", False))
    if s.analysis is not None else st.container()
)
with form_box:
    with card("goal"):
        st.header("1. About you and your goal")
        col1, col2 = st.columns(2)
        program = col1.selectbox("Field of study", list(PROGRAMS), format_func=PROGRAMS.get, key="analyze_program")
        status = col2.selectbox("Status", STATUSES, key="analyze_status")
        background = st.text_area(
            "Where do you study or work now, or where have you worked?",
            max_chars=1500,
            key="analyze_background",
            placeholder="E.g.: third-year student, summer sales internship, student council volunteer",
            height=80,
        )

        st.subheader("Where you want to go")
        target_role = st.text_input("Role", key="target_role", max_chars=120, placeholder="E.g.: Business Analyst, Junior Data Analyst, UX Researcher")
        col1, col2 = st.columns(2)
        company_type = col1.selectbox("Company type", COMPANY_TYPES, key="company_type")
        level = col2.selectbox("Level", LEVELS, key="analyze_level")
        company_details = st.text_input(
            "Specific company or industry (optional)", placeholder="E.g.: Stripe, McKinsey, an EdTech startup",
            max_chars=200, key="analyze_company_details",
        )
        region = st.selectbox("Market", REGIONS, key="analyze_region")
        vacancy_text = st.text_area(
            "Job posting text (highly recommended)",
            max_chars=VACANCY_MAX_CHARS,
            placeholder="Paste the full job posting here: responsibilities and requirements. This improves the advice the most.",
            height=160, key="analyze_vacancy",
        )
        feedback_lang = st.radio("Feedback language", list(FEEDBACK_LANGUAGES), horizontal=True, key="analyze_lang")

    profile = Profile(
        program=program,
        status=status,
        background=background,
        target_role=target_role,
        company_type=company_type,
        company_details=company_details,
        level=level,
        region=region,
        vacancy_text=vacancy_text,
        feedback_language=FEEDBACK_LANGUAGES[feedback_lang],
    )
    clarity, hint = profile.target_clarity()
    if target_role.strip() and clarity == "high":
        st.success(f"Clear goal. {hint}", icon=":material/target:")
    elif target_role.strip():
        st.info(f"Tip: {hint}", icon=":material/lightbulb:")

    with card("cv"):
        st.header("2. Your CV")
        cv = cv_picker("analyze")
        if is_logged_in():
            improve = st.checkbox(
                "Help improve GetCVmax (optional): keep an anonymised copy of this CV and its review",
                value=False, key="improve_consent",
            )
            st.caption(
                "If you tick this, we remove your name, email, phone numbers, links and dates of birth, then keep "
                "the CV text and the review for up to 12 months to check and improve the quality of reviews. It is "
                "not needed to use GetCVmax. Unticking stops saving new copies; delete saved copies any time with "
                "\"Delete my data\" on My account. "
                "[Details](/privacy#improve)"
            )
        else:
            improve = False

    can_run = bool(target_role.strip()) and cv is not None
    limit_caption("analysis")
    if st.button("Review my CV", type="primary", disabled=not can_run) and take_limit("analysis"):
        try:
            with st.spinner("Reviewing your CV. Usually under a minute; at peak times the free model can take 2–3 minutes..."):
                variant = choose_variant("analysis", program, variant_stats_cached("analysis"))
                run = analyze_full(get_client(), profile, cv, addendum=variant_text("analysis", variant))
                s.analysis, s.analysis_run = run.analysis, run
                s["variant"] = variant
            s.cv, s.profile = cv, profile
            s.analysis_id = uuid.uuid4().hex
            s["tier"] = "scan" if billing.billing_enabled() else "full"  # a review always runs as a scan first
            s.pop("feedback_sent", None)
            from_jobs = bool(vacancy_text.strip()) and same_text(vacancy_text, s.get("jobs_pasted", ""))
            s["match_source"] = "jobs" if from_jobs else "form"  # for the match_done event
            log_analysis(s, run, program=program, region=region, level=level, clarity=clarity, variant=variant)
            s.pop("formatted_cv", None)
            s.pop("open_form", None)
            # score_detail: score components as a plain dict (criteria tuples become lists).
            score_detail = {**asdict(run.score), "items": [list(item) for item in run.score.items]}
            s["result_payload"] = {"analysis_id": s.analysis_id, "role": target_role, "company_type": company_type,
                                   "analysis": s.analysis.model_dump(), "score_detail": score_detail}
            save_result("analysis", f"{target_role} · {s.analysis.overall_score}/100",
                        {**s["result_payload"], "tier": s["tier"]})
            if improve:
                save_improvement_sample(
                    {"program": program, "status": status, "level": level, "region": region,
                     "company_type": company_type, "target_role": target_role[:120],
                     "prompt_variant": variant, "model": config.MODEL},
                    cv.text, s.analysis.model_dump())
            s.grill, s.grill_result = None, None
            for k in [k for k in st.session_state if str(k).startswith(("accept_", "match:", "share:"))]:
                del st.session_state[k]  # the edit ticks, the posting match and the share cards of the previous review
            st.rerun()  # collapse the form and show the result on top
        except LLMError as e:
            st.error(str(e))
    if not target_role.strip():
        st.caption("Enter a role to get started.")

if s.analysis is None:
    st.stop()

# ---------- Results ----------
a = s.analysis
billing.show_notice()
if billing.billing_enabled() and s.get("tier") != "full" and s.analysis_id in billing.unlocked_ids():
    mark_full(s)  # unlocked on return from the checkout (app.py) or on another page
locked = billing.is_locked(s)
st.header("Results")
company = "" if s.profile.company_type == ANY_COMPANY else s.profile.company_type
st.caption(" · ".join(md_escape(x) for x in (s.profile.target_role, company, s.cv.filename) if x))
shown_edits = billing.free_edits(a.edits) if locked else all_edits(s)  # locked edits are never rendered
if locked:
    if billing.unlock_panel(s, len(a.edits) - len(shown_edits)):
        mark_full(s)
        st.toast("Unlocked. All edits are below.", icon=":material/lock_open:")
        st.rerun()
tab_overview, tab_grill, tab_edits, tab_gaps, tab_jobs, tab_export = st.tabs(
    ["Overview", "Q&A", "Edits", "Skills to build", "Jobs", "Final CV"]
)

run = s.get("analysis_run")  # None if the result did not come from analyze_full: then no new blocks

with tab_overview:
    st.metric("CV fit for this goal", f"{a.overall_score}/100")
    caption = score_caption(run, s.profile.program)
    if caption:
        st.caption(caption)
    render_share(s)
    st.write(md_escape(a.summary))
    if locked:
        if len((s.profile.vacancy_text or "").strip()) >= MATCH_MIN_CHARS:
            billing.locked_note("Matching your CV to the posting")
    elif len((s.profile.vacancy_text or "").strip()) >= MATCH_MIN_CHARS:
        render_match(s)
    else:
        st.caption("Tip: paste a job posting into the form above to see how well your CV matches it.")
    with st.expander("How I understood your goal (fix it in the form if it's off)"):
        for t in a.target_assumptions:
            st.markdown(f"- {md_escape(t)}")
    if run is not None and run.checks is not None:
        render_checks(run.checks)
    st.subheader("Scores by criterion")
    for c in a.scores:
        st.markdown(f"**{md_escape(c.criterion)}**: {'●' * c.score}{'○' * (5 - c.score)}  {md_escape(c.comment)}")
    flagged = [v for v in a.line_review if v.verdict != "keep"]
    if a.line_review:
        st.subheader("Line-by-line check")
        n = len(a.line_review)
        st.caption(
            f"Checked {plural(n, 'line')}. "
            + (f"{len(flagged)} {'has' if len(flagged) == 1 else 'have'} issues, see the Edits tab." if flagged
               else "No line-level issues.")
        )
        verdict_label = {"cut": ":red-badge[Cut]", "shorten": ":red-badge[Shorten]",
                         "rewrite": ":red-badge[Rewrite]", "move": ":red-badge[Move]"}
        for v in flagged:
            st.markdown(f"- {verdict_label.get(v.verdict, v.verdict)} {md_escape(v.line)}  \n  {md_escape(v.reason)}")
    if a.strengths:
        st.subheader("What already works")
        for x in a.strengths:
            st.markdown(f"- {md_escape(x)}")
    if s.grill is None and not locked:
        st.info(
            f"**Next: Q&A.** Up to {config.GRILL_MAX_QUESTIONS} short questions about your experience. "
            "GetCVmax uses your answers to find numbers and facts missing from your CV and turns them into stronger edits.",
            icon=":material/forum:",
        )
    with card("rate-analysis"):
        st.markdown("**Was this review useful?**")
        rated = st.feedback("thumbs", key=f"rate_{s.analysis_id}")
        if rated is not None and s.get("rated_value") != (s.analysis_id, rated):
            s["rated_value"] = (s.analysis_id, rated)
            log_event("analysis_rated", analysis_id=s.analysis_id, rating=rated)
            if s.get("rated_analysis") != s.analysis_id:
                s["rated_analysis"] = s.analysis_id
                send_feedback("analysis", rated, f"{s.profile.program} · {s.profile.target_role}"[:200])
            st.toast("Thanks for the rating!")
        st.page_link("views/feedback.py", label="Tell us more", icon=":material/chat:")

def known_facts(s) -> str:
    """Everything the user said about themselves: the CV, onboarding and the questionnaire answers."""
    parts = [s.cv.text, s.profile.background]
    if s.grill:
        parts += [t.answer for t in s.grill.turns]
    return "\n".join(parts)


with tab_edits:
    removed_edits, trimmed_edits = verification_totals(s)
    note = verification_caption(removed_edits, trimmed_edits)
    if note:
        st.caption(note)
    if locked:
        pass
    elif not all_edits(s) and s.grill_result is None:
        st.info(
            "No edits yet: your CV already reads well for this goal. To get edits, do the **Q&A** "
            "(previous tab): GetCVmax will turn your answers into numbers and facts for your CV.",
            icon=":material/forum:",
        )
    elif s.grill_result is None:
        st.info(
            "These edits come from your CV text only. The strongest ones appear after the **Q&A** (previous tab).",
            icon=":material/forum:",
        )
    if shown_edits and not locked:
        st.caption("Tick the edits you accept. Replace anything in [brackets] with your own details, or remove it if it isn't true.")
    facts = known_facts(s)
    for i, e in enumerate(shown_edits):
        with card(f"edit-{i}"):
            st.markdown(f"**{md_escape(e.section)}** · {PRIORITY_LABEL[e.priority]}")
            c1, c2 = st.columns(2)
            with c1.container(key=f"before-{i}"):
                st.caption("BEFORE")
                st.markdown(md_escape(e.before) or "_(new line)_")
            with c2.container(key=f"after-{i}"):
                st.caption("AFTER")
                st.markdown(md_escape(e.after) or "_(remove)_")
            st.caption(md_escape(e.reason))
            swapped = changed_action(e.before, e.after) if e.before and e.after else None
            if swapped:
                st.warning(
                    f"This edit changes what you did: «{md_escape(swapped[0])}» became «{md_escape(swapped[1])}». "
                    "Accept it only if that is really what you did; a recruiter may ask about it."
                )
            dropped = lost_facts(e.before, e.after)
            if dropped:
                st.warning(
                    "This edit drops facts from the line: " + ", ".join(dropped)
                    + ". If they're true and important, skip this edit or add them back into the text."
                )
            flagged = unverified_terms(e.after, facts)
            if flagged:
                st.warning(
                    "This isn't in your CV or your answers: " + ", ".join(flagged)
                    + ". Keep only what's true."
                )
            if not locked:
                st.checkbox("Accept", key=f"accept_{i}")
    if locked:
        hidden = len(a.edits) - len(shown_edits)
        if hidden > 0:
            st.info(f"{hidden} more {'edit is' if hidden == 1 else 'edits are'} locked. Unlock the full fix above to see "
                    "them.", icon=":material/lock:")

with tab_gaps:
    if locked:
        billing.locked_note("The skills plan")
    else:
        demand = skill_demand_cached(s.profile.program, s.profile.region)
        if demand and demand[0].get("total_postings"):
            total = demand[0]["total_postings"]
            top = ", ".join(f"{md_escape(str(d['skill']))} {round(100 * d['postings'] / total)}%" for d in demand[:8])
            st.caption(f"In the last {demand[0].get('window_days')} days, of {total} postings for your field in "
                       f"{md_escape(s.profile.region)}: {top}")
        st.caption("What to do beyond your CV to boost your chances, most important first.")
        with st.expander("How we pick resources", icon=":material/verified:"):
            render_how_we_pick()
        try:
            gap_skill_ids = [match_skills(f"{g.item}. {g.how_to_close}") for g in a.gaps]
        except CatalogError:
            gap_skill_ids = None
            st.info(SOFT_ERROR, icon=":material/info:")
        learn_key = f"learn_{s.analysis_id}"
        budget, hours = ("free", 5)
        if gap_skill_ids is not None and a.gaps:
            budget, hours = render_controls(learn_key)
        for j in order_gaps(a.gaps, gap_skill_ids or [[] for _ in a.gaps], demand):
            g = a.gaps[j]
            with card(f"gap-{j}"):
                st.markdown(f"**{md_escape(g.item)}** · {PRIORITY_LABEL[g.impact]} · {md_escape(g.time_estimate)}")
                st.markdown(md_escape(g.why_it_matters))
                st.markdown(f"**How:** {md_escape(g.how_to_close)}")
                if gap_skill_ids is not None:
                    st.markdown("**Learn it**")
                    render_resources(
                        gap_skill_ids[j], key=f"{learn_key}_g{j}", feedback_language=s.profile.feedback_language,
                        budget=budget, hours_per_week=hours, demand=demand, missing_text=g.item, scope=learn_key,
                    )

with tab_jobs:
    st.caption("Live postings for this goal, ranked by fit to your CV.")
    # The company board only by an explicit slug; we do not pass the free text company_details to third-party APIs
    board = st.text_input("Company careers board (optional)", placeholder="E.g.: stripe",
                          max_chars=40, key="analyze_board_slug").strip().lower()
    if board and not re.fullmatch(r"[a-z0-9-]{2,40}", board):
        st.caption("Use only letters, digits and hyphens, as in the board URL.")
        board = ""
    render_jobs(
        key="analyze", role=s.profile.target_role, keywords=[s.profile.target_role], region=s.profile.region,
        level=s.profile.level, company=board, cv_text=s.cv.text,
        gaps=[] if locked else [g.item for g in a.gaps], feedback_language=s.profile.feedback_language,
        on_review=review_for_posting,
    )

with tab_grill:
    if locked:
        billing.locked_note("The Q&A")
    else:
        st.caption(
            f"Up to {config.GRILL_MAX_QUESTIONS} questions about your experience: numbers, scale and what's missing from your CV. "
            "Your answers become stronger edits. Nothing is made up, GetCVmax only uses what you say. "
            "You can skip questions and finish at any time."
        )
        if s.get("grill_error"):  # an error from the previous pass, before st.rerun()
            st.error(s.pop("grill_error"))
        if s.grill is None:
            if st.button("Start Q&A", type="primary") and take_limit("grill"):
                g = GrillSession()
                try:
                    with st.spinner("Thinking of the first question..."):
                        next_question(get_client(), s.profile, s.cv, g)
                    s.grill = g
                    st.rerun()
                except LLMError as e:
                    st.error(str(e))
        else:
            g = s.grill
            if g.finished and not g.turns:
                st.info("GetCVmax found nothing to ask about: your CV already has numbers and details. "
                        "Go to the Edits tab.")
            for i, t in enumerate(g.turns, 1):
                if g.finished and not t.answer:
                    continue
                with st.chat_message("assistant", avatar=BOT_AVATAR):
                    st.markdown(f"**{i}.** {md_escape(t.question)}")
                    st.caption(md_escape(t.why_asking))
                if t.answer:
                    with st.chat_message("user"):
                        st.markdown(md_escape(t.answer))
            if g.pending is not None:
                with st.form("grill_answer", clear_on_submit=True):
                    reply = st.text_area("Your answer", max_chars=1500)
                    c1, c2 = st.columns(2)
                    send = c1.form_submit_button("Answer", type="primary")
                    skip = c2.form_submit_button("Skip")
                if send or skip:
                    # We take the question kind before asking for the next one.
                    asked_kind = g.turns[-1].kind
                    log_event("grill_turn", analysis_id=s.analysis_id, kind=asked_kind, answered=not skip)
                    answer(g, "" if skip else reply)
                    try:
                        with st.spinner("Next question..."):
                            next_question(get_client(), s.profile, s.cv, g)
                    except LLMError as e:
                        s["grill_error"] = str(e)
                    st.rerun()
            elif not g.finished and g.turns and len(g.turns) < g.max_questions and s.grill_result is None:
                # The next question did not arrive (the model was busy): we let the user try again.
                if st.button("Next question"):
                    try:
                        with st.spinner("Next question..."):
                            next_question(get_client(), s.profile, s.cv, g)
                        st.rerun()
                    except LLMError as e:
                        st.error(str(e))
            if s.grill_result is None and any(t.answer for t in g.turns):
                if st.button("Finish and get edits", type="primary" if g.pending is None else "secondary"):
                    try:
                        with st.spinner("Turning your answers into edits..."):
                            s.grill_result = finalize(get_client(), s.profile, s.cv, g)
                        st.rerun()
                    except LLMError as e:
                        st.error(str(e))
            if s.grill_result is not None:
                st.success(f"Done: {plural(len(s.grill_result.edits), 'new edit')} added to the Edits tab.")

with tab_export:
    if locked:
        billing.locked_note("The final CV and exports")
    else:
        edits = all_edits(s)
        accepted = latest_per_line([e for i, e in enumerate(edits) if st.session_state.get(f"accept_{i}")])
        if not accepted:
            st.info("First, accept at least one edit in the Edits tab.")
        elif not s.cv.text.strip():
            st.warning("Couldn't extract text from this PDF (it looks like a scan). Use the list of edits below.")
            st.download_button("Download list of edits (.md)", changes_markdown(accepted), "cvmax_changes.md",
                               on_click=log_accepted_edits, args=(s,))
        else:
            report = apply_edits(s.cv.text, accepted)
            if report.not_found:
                st.warning(
                    f"Couldn't place {plural(len(report.not_found), 'edit')} in the text automatically. "
                    "Add them by hand, they're in the list of edits."
                )
            with st.expander("CV text with edits", expanded=False):
                text = st.text_area("You can tweak it before formatting", report.text, height=400, max_chars=40_000,
                                    key=f"export_text_{hash(report.text)}")

            st.subheader("Formatted CV")
            st.caption("One recruiter-tested template: sans-serif font, clear sections, dates on the right.")
            formatted = s.get("formatted_cv")
            if formatted is None or formatted[0] != text:
                if st.button("Format my CV (PDF and DOCX)", type="primary") and take_limit("export"):
                    try:
                        with st.spinner("Splitting your CV into sections..."):
                            s["formatted_cv"] = (text, structure_cv(get_client(), text))
                        log_accepted_edits(s)
                        st.rerun()
                    except LLMError as e:
                        st.error(str(e))
            else:
                built = formatted[1]
                changed = changed_bullets(text, built)
                if changed:
                    st.warning(
                        "These lines in the formatted CV differ from your text. Check them before you send it:\n\n"
                        + "\n".join(f"- {md_escape(b)}" for b in changed)
                    )
                holes = has_placeholders(built)
                if holes:
                    st.warning("Replace or remove the placeholders in these lines:\n\n" + "\n".join(f"- {md_escape(h)}" for h in holes))
                pdf = render_pdf(built)
                with card("export-preview"):
                    for page in pdf_preview(pdf):
                        st.image(page, width="stretch")
                name = built.full_name.replace(" ", "_") or "cv"
                c1, c2 = st.columns(2)
                c1.download_button("Download PDF", pdf, f"{name}_CV.pdf", mime="application/pdf",
                                   type="primary", on_click=log_export, args=(s, "pdf"))
                c2.download_button("Download DOCX", render_docx(built), f"{name}_CV.docx",
                                   on_click=log_export, args=(s, "docx"))
                st.caption("Open the DOCX in Word or Google Docs if you want to change anything by hand.")

            st.divider()
            c1, c2 = st.columns(2)
            c1.download_button("CV text (.txt)", text, "cv_cvmax.txt", on_click=log_accepted_edits, args=(s,))
            c2.download_button("List of edits (.md)", changes_markdown(accepted), "cvmax_changes.md",
                               on_click=log_accepted_edits, args=(s,))
