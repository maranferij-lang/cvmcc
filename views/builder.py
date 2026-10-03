"""Конструктор CV: перше CV з нуля."""

import streamlit as st

from cvmax import config
from cvmax.builder import BuilderDraft, build_cv, next_builder_question
from cvmax.cv_render import pdf_preview, render_docx, render_pdf
from cvmax.grill import GrillSession, answer
from cvmax.llm import LLMError
from cvmax.profile import FEEDBACK_LANGUAGES, PROGRAMS, STATUSES
from cvmax.safe_text import md_escape
from ui.account import current_email, limit_caption, profile_value, require_login, save_result, take_limit
from ui.common import BOT_AVATAR, card, consent, demo_banner, get_llm

require_login("CV builder")
s = st.session_state
st.title("CV builder")
st.caption(
    "No CV yet? Fill in the basics, tell us about yourself in your own words, answer a few questions, "
    "and GetCVmax builds an English CV with the right structure."
)
demo_banner()
if not consent("builder"):
    st.stop()

# ---------- 1. Основне ----------
with card("basics"):
    st.header("1. The basics")
    c1, c2 = st.columns(2)
    full_name = c1.text_input("Full name", max_chars=80, placeholder="Alex Morgan")
    email = c2.text_input("Email", max_chars=120, value=current_email() or "", placeholder="alex@example.com")
    c1, c2 = st.columns(2)
    phone = c1.text_input("Phone (optional)", max_chars=40)
    city = c2.text_input("City", max_chars=80, placeholder="Berlin, Germany")
    links = st.text_input("LinkedIn, GitHub or portfolio (optional)", max_chars=300, placeholder="linkedin.com/in/alexmorgan")

    programs = list(PROGRAMS)
    default_program = profile_value("program")
    c1, c2, c3 = st.columns([2, 1, 1])
    program = c1.selectbox("Field of study", programs, format_func=PROGRAMS.get,
                           index=programs.index(default_program) if default_program in programs else 0)
    default_status = profile_value("status")
    status = c2.selectbox("Status", STATUSES, index=STATUSES.index(default_status) if default_status in STATUSES else 0)
    grad_year = c3.text_input("Graduation year", max_chars=10, placeholder="2027")
    c1, c2 = st.columns(2)
    gpa = c1.text_input("GPA (optional)", max_chars=40, placeholder="3.7/4.0 or 92/100")
    target_role = c2.text_input("Target role (optional)", max_chars=120, value=profile_value("goal", ""))

# ---------- 2. Про себе ----------
with card("story"):
    st.header("2. Tell us about yourself")
    notes = st.text_area(
        "In your own words, any language is fine",
        max_chars=4000,
        value=profile_value("background", ""),
        placeholder=(
            "Where you've worked or interned, what you did there and what you achieved. Class and personal projects. "
            "Student clubs, volunteering, case competitions, olympiads. Skills and tools. Languages and level."
        ),
        height=200,
    )
    lang = st.radio("Tips language", list(FEEDBACK_LANGUAGES), horizontal=True, key="builder_lang")

draft = BuilderDraft(
    full_name=full_name, email=email, phone=phone, city=city, links=links, program=program, status=status,
    grad_year=grad_year, gpa=gpa, target_role=target_role, notes=notes,
    feedback_language=FEEDBACK_LANGUAGES[lang],
)
ready = bool(full_name.strip() and email.strip() and len(notes.strip()) >= 30)
if not ready:
    st.caption("To continue, enter your name and email and write at least a few sentences about yourself.")

# ---------- 3. Інтерв'ю ----------
st.header("3. A few questions")
g: GrillSession | None = s.get("builder_grill")
if g is None:
    limit_caption("builder")
    if st.button("Start", type="primary", disabled=not ready):
        if take_limit("builder"):
            s["builder_grill"] = GrillSession()
            s["builder_draft"] = draft
            try:
                with st.spinner("Reading your story..."):
                    next_builder_question(get_llm(), draft, s["builder_grill"])
            except LLMError as e:
                st.error(str(e))
            st.rerun()
    st.stop()

draft = s.get("builder_draft", draft)
for i, t in enumerate(g.turns, 1):
    if g.finished and not t.answer:
        continue
    with st.chat_message("assistant", avatar=BOT_AVATAR):
        st.markdown(f"**{i}.** {md_escape(t.question)}")
    if t.answer:
        with st.chat_message("user"):
            st.markdown(md_escape(t.answer))
if g.pending is not None:
    with st.form("builder_answer", clear_on_submit=True):
        reply = st.text_area("Your answer", max_chars=1500)
        c1, c2 = st.columns(2)
        send = c1.form_submit_button("Answer", type="primary")
        skip = c2.form_submit_button("Skip")
    if send or skip:
        answer(g, "" if skip else reply)
        try:
            with st.spinner("Next question..."):
                next_builder_question(get_llm(), draft, g)
        except LLMError as e:
            st.error(str(e))
        st.rerun()
    st.caption(f"Question {len(g.turns)} of {config.GRILL_MAX_QUESTIONS}. You can build your CV at any time.")

if st.button("Build my CV", type="primary" if g.pending is None else "secondary") and take_limit("build"):
    try:
        with st.spinner("Building your CV, this takes up to a minute..."):
            s["builder_cv"] = build_cv(get_llm(), draft, g)
        save_result("builder", draft.target_role or draft.full_name, {"cv": s["builder_cv"].model_dump()})
    except LLMError as e:
        st.error(str(e))

# ---------- 4. Результат ----------
cv = s.get("builder_cv")
if cv is None:
    st.stop()
st.header("4. Your CV")
if cv.notes_for_user:
    with card("todo"):
        st.markdown("**Still to do**")
        for n in cv.notes_for_user:
            st.markdown(f"- {md_escape(n)}")
pdf = render_pdf(cv)
with card("preview"):
    for page in pdf_preview(pdf):
        st.image(page, width="stretch")
name = cv.full_name.replace(" ", "_") or "cv"
c1, c2, c3 = st.columns(3)
c1.download_button("Download PDF", pdf, f"{name}_CV.pdf", mime="application/pdf", type="primary")
c2.download_button("Download DOCX", render_docx(cv), f"{name}_CV.docx")
if c3.button("Start over"):
    for k in ("builder_grill", "builder_draft", "builder_cv"):
        s.pop(k, None)
    st.rerun()
st.caption(
    "If your CV has [brackets], download the DOCX, replace them in Word or Google Docs and save as PDF. "
    "Then check the result against a specific job on the CV review page."
)
