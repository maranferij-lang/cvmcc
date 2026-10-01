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

require_login("Конструктор CV")
s = st.session_state
st.title("Конструктор CV")
st.caption(
    "Немає CV? Заповни основне, розкажи про себе своїми словами, дай відповідь на кілька питань, "
    "і CVmax збере CV англійською в правильній структурі."
)
demo_banner()
if not consent("builder"):
    st.stop()

# ---------- 1. Основне ----------
with card("basics"):
    st.header("1. Основне")
    c1, c2 = st.columns(2)
    full_name = c1.text_input("Ім'я та прізвище латиницею", max_chars=80, placeholder="Olena Petrenko")
    email = c2.text_input("Email", max_chars=120, value=current_email() or "", placeholder="olena@example.com")
    c1, c2 = st.columns(2)
    phone = c1.text_input("Телефон (необов'язково)", max_chars=40)
    city = c2.text_input("Місто", max_chars=80, value="Kyiv, Ukraine")
    links = st.text_input("LinkedIn, GitHub чи портфоліо (необов'язково)", max_chars=300, placeholder="linkedin.com/in/olena")

    programs = list(PROGRAMS)
    default_program = profile_value("program")
    c1, c2, c3 = st.columns([2, 1, 1])
    program = c1.selectbox("Програма в КШЕ", programs, format_func=PROGRAMS.get,
                           index=programs.index(default_program) if default_program in programs else 0)
    default_status = profile_value("status")
    status = c2.selectbox("Статус", STATUSES, index=STATUSES.index(default_status) if default_status in STATUSES else 0)
    grad_year = c3.text_input("Рік випуску", max_chars=10, placeholder="2027")
    c1, c2 = st.columns(2)
    gpa = c1.text_input("Середній бал (необов'язково)", max_chars=40, placeholder="3.7/4.0 або 92/100")
    target_role = c2.text_input("Роль, на яку цілишся (необов'язково)", max_chars=120, value=profile_value("goal", ""))

# ---------- 2. Про себе ----------
with card("story"):
    st.header("2. Розкажи про себе")
    notes = st.text_area(
        "Своїми словами, можна українською",
        max_chars=4000,
        value=profile_value("background", ""),
        placeholder=(
            "Де працював(-ла) чи стажувався(-лась), що там робив(-ла) і чого досяг(-ла). Навчальні й особисті проєкти. "
            "Студрада, волонтерство, кейс-чемпіонати, олімпіади. Навички й програми. Мови і рівень."
        ),
        height=200,
    )
    lang = st.radio("Мова підказок", list(FEEDBACK_LANGUAGES), horizontal=True, key="builder_lang")

draft = BuilderDraft(
    full_name=full_name, email=email, phone=phone, city=city, links=links, program=program, status=status,
    grad_year=grad_year, gpa=gpa, target_role=target_role, notes=notes,
    feedback_language=FEEDBACK_LANGUAGES[lang],
)
ready = bool(full_name.strip() and email.strip() and len(notes.strip()) >= 30)
if not ready:
    st.caption("Щоб продовжити, вкажи ім'я, email і розкажи про себе хоча б кілька речень.")

# ---------- 3. Інтерв'ю ----------
st.header("3. Кілька питань")
g: GrillSession | None = s.get("builder_grill")
if g is None:
    limit_caption("builder")
    if st.button("Почати", type="primary", disabled=not ready):
        if take_limit("builder"):
            s["builder_grill"] = GrillSession()
            s["builder_draft"] = draft
            try:
                with st.spinner("Читаю твою розповідь..."):
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
        reply = st.text_area("Твоя відповідь", max_chars=1500)
        c1, c2 = st.columns(2)
        send = c1.form_submit_button("Відповісти", type="primary")
        skip = c2.form_submit_button("Пропустити")
    if send or skip:
        answer(g, "" if skip else reply)
        try:
            with st.spinner("Наступне питання..."):
                next_builder_question(get_llm(), draft, g)
        except LLMError as e:
            st.error(str(e))
        st.rerun()
    st.caption(f"Питання {len(g.turns)} з {config.GRILL_MAX_QUESTIONS}. Можна зібрати CV будь-коли.")

if st.button("Зібрати CV", type="primary" if g.pending is None else "secondary") and take_limit("build"):
    try:
        with st.spinner("Збираю CV, це займає до хвилини..."):
            s["builder_cv"] = build_cv(get_llm(), draft, g)
        save_result("builder", draft.target_role or draft.full_name, {"cv": s["builder_cv"].model_dump()})
    except LLMError as e:
        st.error(str(e))

# ---------- 4. Результат ----------
cv = s.get("builder_cv")
if cv is None:
    st.stop()
st.header("4. Твоє CV")
if cv.notes_for_user:
    with card("todo"):
        st.markdown("**Що доробити**")
        for n in cv.notes_for_user:
            st.markdown(f"- {md_escape(n)}")
pdf = render_pdf(cv)
with card("preview"):
    for page in pdf_preview(pdf):
        st.image(page, width="stretch")
name = cv.full_name.replace(" ", "_") or "cv"
c1, c2, c3 = st.columns(3)
c1.download_button("Завантажити PDF", pdf, f"{name}_CV.pdf", mime="application/pdf", type="primary")
c2.download_button("Завантажити DOCX", render_docx(cv), f"{name}_CV.docx")
if c3.button("Почати заново"):
    for k in ("builder_grill", "builder_draft", "builder_cv"):
        s.pop(k, None)
    st.rerun()
st.caption(
    "Якщо в CV є [дужки], завантаж DOCX, заміни їх у Word або Google Docs і збережи як PDF. "
    "Потім перевір результат на сторінці «Аналіз CV» під конкретну вакансію."
)
