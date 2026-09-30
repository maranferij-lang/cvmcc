"""Сторінка «Куди податись»: напрями, де з цим CV найбільше шансів."""

from __future__ import annotations

import streamlit as st

from cvmax.career import CareerPrefs, match_careers
from cvmax.llm import LLMError
from cvmax.profile import FEEDBACK_LANGUAGES, LEVELS, PROGRAMS, REGIONS, STATUSES
from ui.common import consent, cv_picker, demo_banner, get_llm

JOB_BOARDS = "LinkedIn, Djinni, Work.ua, DOU, Robota.ua і кар'єрний центр КШЕ"

st.title("Куди податись")
st.caption(
    "Є CV, але не знаєш, куди з ним іти? Я подивлюсь на твій досвід і запропоную напрями, "
    "де в тебе найбільше шансів отримати офер уже зараз."
)
demo_banner()
if not consent("career"):
    st.stop()

st.header("1. Трохи про тебе")
c1, c2 = st.columns(2)
program = c1.selectbox("Програма в КШЕ", list(PROGRAMS), format_func=PROGRAMS.get, key="career_program")
status = c2.selectbox("Статус", STATUSES, key="career_status")
interests = st.text_area(
    "Що тобі цікаво? (необов'язково)",
    placeholder="Напр.: люблю працювати з даними, цікавлять фінанси і стартапи",
    height=70,
)
avoid = st.text_input("Чого точно не хочеш? (необов'язково)", placeholder="Напр.: холодні дзвінки, чистий код")
c1, c2 = st.columns(2)
level = c1.selectbox("Рівень", LEVELS, key="career_level")
region = c2.selectbox("Ринок", REGIONS, key="career_region")
lang = st.radio("Мова порад", list(FEEDBACK_LANGUAGES), horizontal=True, key="career_lang")

st.header("2. Твоє CV")
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
if st.button("Знайти напрями", type="primary", disabled=cv is None):
    try:
        with st.spinner("Дивлюсь, де твій досвід цінують найбільше..."):
            st.session_state["career_result"] = match_careers(get_llm(), prefs, cv)
    except LLMError as e:
        st.error(str(e))

result = st.session_state.get("career_result")
if result is None:
    st.stop()

st.header("3. Твої напрями")
st.write(result.candidate_summary)
if result.strongest_assets:
    st.markdown("**Твої найсильніші сторони:** " + "; ".join(result.strongest_assets))
st.info(result.general_advice)

for i, d in enumerate(result.directions):
    with st.container(border=True):
        top_l, top_r = st.columns([3, 1])
        top_l.subheader(d.role)
        top_l.caption(d.company_type)
        top_r.metric("Шанс", f"{d.fit_score}%")
        st.progress(d.fit_score / 100)
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Чому підходить**")
            for x in d.why_fits:
                st.markdown(f"- {x}")
        with c2:
            st.markdown("**Чого бракує**")
            for x in d.gaps:
                st.markdown(f"- {x}")
        st.markdown("**Що зробити цього місяця**")
        for x in d.first_steps:
            st.markdown(f"- {x}")
        st.markdown("**Що шукати:** " + ", ".join(f"`{k}`" for k in d.search_keywords))
        if st.button("Покращити CV під цей напрям", key=f"go_analyze_{i}"):
            st.session_state["prefill_role"] = d.role
            st.session_state["prefill_company"] = d.company_type
            st.switch_page("views/analyze.py")

st.caption(f"Де шукати вакансії: {JOB_BOARDS}. Шанс це оцінка моделі, а не гарантія.")
