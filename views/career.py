"""The "Where to apply" page: directions where this CV has the best chances."""

from __future__ import annotations

import streamlit as st

from cvmax.career import CareerPrefs, match_careers
from cvmax.llm import LLMError
from cvmax.profile import FEEDBACK_LANGUAGES, LEVELS, PROGRAMS, REGIONS, STATUSES
from cvmax.safe_text import md_escape
from ui.account import limit_caption, profile_value, require_login, save_result, take_limit
from ui.jobs import render_jobs
from ui.common import card, consent, cv_picker, demo_banner, get_llm

require_login("Where to apply")
if not st.session_state.get("career_defaults_set"):
    st.session_state["career_defaults_set"] = True
    if profile_value("program") in PROGRAMS:
        st.session_state["career_program"] = profile_value("program")
    if profile_value("status") in STATUSES:
        st.session_state["career_status"] = profile_value("status")
    st.session_state["career_interests"] = profile_value("goal", "")

st.title("Where to apply")
st.caption(
    "Have a CV but not sure where to take it? GetCVmax looks at your experience and suggests directions "
    "where you have the best shot at an offer right now."
)
demo_banner()
if not consent("career"):
    st.stop()

# After the result the form collapses, so the directions are right under the heading.
form_box = (
    st.expander("About you and your CV: edit and search again", icon=":material/tune:")
    if st.session_state.get("career_result") is not None else st.container()
)
with form_box:
    with card("about-you"):
        st.header("1. A bit about you")
        c1, c2 = st.columns(2)
        program = c1.selectbox("Field of study", list(PROGRAMS), format_func=PROGRAMS.get, key="career_program")
        status = c2.selectbox("Status", STATUSES, key="career_status")
        interests = st.text_area(
            "What interests you? (optional)",
            max_chars=600,
            key="career_interests",
            placeholder="E.g.: I like working with data, interested in finance and startups",
            height=70,
        )
        avoid = st.text_input("Anything you definitely don't want? (optional)", max_chars=300, placeholder="E.g.: cold calling, pure coding", key="career_avoid")
        c1, c2 = st.columns(2)
        level = c1.selectbox("Level", LEVELS, key="career_level")
        region = c2.selectbox("Market", REGIONS, key="career_region")
        lang = st.radio("Feedback language", list(FEEDBACK_LANGUAGES), horizontal=True, key="career_lang")

    with card("career-cv"):
        st.header("2. Your CV")
        cv = cv_picker("career")

    prefs = CareerPrefs(
        program=program,
        status=status,
        interests=interests,
        avoid=avoid,
        level=level,
        region=region,
        feedback_language=FEEDBACK_LANGUAGES[lang],
    )
    limit_caption("career")
    if st.button("Find directions", type="primary", disabled=cv is None) and take_limit("career"):
        try:
            with st.spinner("Looking for where your experience is valued most..."):
                st.session_state["career_result"] = match_careers(get_llm(), prefs, cv)
            st.session_state["career_cv_text"] = cv.text
            st.session_state["career_prefs"] = prefs
            top = st.session_state["career_result"].directions
            save_result("career", ", ".join(d.role for d in top[:2]) or "Directions",
                        {"career": st.session_state["career_result"].model_dump()})
            st.rerun()  # collapse the form and show the directions on top
        except LLMError as e:
            st.error(str(e))

result = st.session_state.get("career_result")
if result is None:
    st.stop()

st.header("Your directions")
st.markdown(md_escape(result.candidate_summary))
if result.strongest_assets:
    st.markdown("**Your strongest assets:** " + "; ".join(md_escape(x) for x in result.strongest_assets))
st.info(md_escape(result.general_advice))

prefs_used = st.session_state.get("career_prefs", prefs)
for i, d in enumerate(result.directions):
    with card(f"direction-{i}"):
        top_l, top_r = st.columns([3, 1])
        top_l.subheader(md_escape(d.role))
        top_l.caption(md_escape(d.company_type))
        top_r.metric("Chance", f"{d.fit_score}%")
        st.progress(d.fit_score / 100)
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Why it fits**")
            for x in d.why_fits:
                st.markdown(f"- {md_escape(x)}")
        with c2:
            st.markdown("**What's missing**")
            for x in d.gaps:
                st.markdown(f"- {md_escape(x)}")
        st.markdown("**What to do this month**")
        for x in d.first_steps:
            st.markdown(f"- {md_escape(x)}")
        st.markdown("**Search for:** " + ", ".join(f"`{k.replace('`', '')}`" for k in d.search_keywords))
        render_jobs(
            key=f"career-{i}", role=d.role, keywords=d.search_keywords, region=prefs_used.region, level=prefs_used.level,
            company="", cv_text=st.session_state.get("career_cv_text", ""), gaps=d.gaps,
            feedback_language=prefs_used.feedback_language,
        )
        if st.button("Tailor my CV to this direction", key=f"go_analyze_{i}"):
            st.session_state["prefill_role"] = d.role
            st.session_state["prefill_company"] = d.company_type
            st.switch_page("views/analyze.py")

st.caption(
    "Live vacancies come from DOU, Djinni, Arbeitnow, Remotive, Jobicy (and Jooble or Adzuna when enabled); "
    "LinkedIn, Indeed, Glassdoor, Work.ua and Robota.ua open as searches. "
    "The chance is the model's estimate, not a guarantee."
)
