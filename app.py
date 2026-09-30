"""CVMAX: вебзастосунок. Запуск: streamlit run app.py"""

from __future__ import annotations

import os

import streamlit as st

from cvmax import config
from cvmax.analyze import analyze_cv
from cvmax.cv_input import CVFile, CVReadError, load_cv
from cvmax.demo import DEMO_CV_TEXT, FakeClient
from cvmax.edits import apply_edits, changes_markdown, text_to_docx
from cvmax.grill import GrillSession, answer, finalize, next_question
from cvmax.llm import LLMError, make_client
from cvmax.profile import COMPANY_TYPES, FEEDBACK_LANGUAGES, LEVELS, PROGRAMS, REGIONS, STATUSES, Profile

st.set_page_config(page_title="CVMAX", page_icon="📄", layout="centered")

PRIORITY_LABEL = {"high": "🔴 важливо", "medium": "🟡 бажано", "low": "⚪ дрібниця"}


def get_api_key() -> str | None:
    try:
        key = st.secrets.get("ANTHROPIC_API_KEY")
    except Exception:  # немає secrets.toml
        key = None
    return key or os.environ.get("ANTHROPIC_API_KEY")


DEMO = os.environ.get("CVMAX_DEMO") == "1" or not get_api_key()


@st.cache_resource
def get_client():
    return FakeClient() if DEMO else make_client(get_api_key())


def state():
    s = st.session_state
    s.setdefault("analysis", None)
    s.setdefault("cv", None)
    s.setdefault("profile", None)
    s.setdefault("grill", None)
    s.setdefault("grill_result", None)
    return s


def all_edits(s):
    edits = list(s.analysis.edits) if s.analysis else []
    if s.grill_result:
        edits += list(s.grill_result.edits)
    return edits


# ---------- Шапка ----------
s = state()
st.title("CVMAX")
st.caption("AI-помічник для CV. Пілот для студентів КШЕ, безплатно.")
if DEMO:
    st.info("Демо-режим: API-ключ не налаштовано, тому відповіді заготовлені. Так можна подивитись інтерфейс.")

# ---------- Згода ----------
consent = st.checkbox(
    "Я погоджуюсь на обробку мого CV. CV містить персональні дані. Він надсилається в Claude API "
    "(Anthropic) тільки для аналізу, CVMAX його ніде не зберігає і нікому не передає. "
    "Після закриття вкладки дані зникають."
)
if not consent:
    st.stop()

# ---------- Онбординг ----------
st.header("1. Про тебе і твою ціль")
col1, col2 = st.columns(2)
program = col1.selectbox("Програма в КШЕ", list(PROGRAMS), format_func=PROGRAMS.get)
status = col2.selectbox("Статус", STATUSES)
background = st.text_area(
    "Де ти зараз вчишся, працюєш або працював(-ла)?",
    placeholder="Напр.: 3 курс, літнє стажування в продажах, волонтер у студраді",
    height=80,
)

st.subheader("Куди хочеш потрапити")
target_role = st.text_input("Роль", placeholder="Напр.: Business Analyst, Junior Data Analyst, UX Researcher")
col1, col2 = st.columns(2)
company_type = col1.selectbox("Тип компанії", COMPANY_TYPES)
level = col2.selectbox("Рівень", LEVELS)
company_details = st.text_input(
    "Конкретна компанія або індустрія (необов'язково)", placeholder="Напр.: Monobank, McKinsey, EdTech-стартап"
)
region = st.selectbox("Ринок", REGIONS)
vacancy_text = st.text_area(
    "Текст вакансії (дуже бажано)",
    placeholder="Встав сюди повний опис вакансії: обов'язки і вимоги. Це найбільше покращує поради.",
    height=160,
)
feedback_lang = st.radio("Мова порад", list(FEEDBACK_LANGUAGES), horizontal=True)

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
{"висока": st.success, "середня": st.warning, "низька": st.error}[clarity](f"Чіткість цілі: {clarity}. {hint}")

st.header("2. Твоє CV")
uploaded = st.file_uploader("PDF або DOCX, англійською", type=["pdf", "docx"])

can_run = bool(target_role.strip()) and (uploaded is not None or DEMO)
if st.button("Проаналізувати CV", type="primary", disabled=not can_run):
    try:
        if uploaded is not None:
            data = uploaded.getvalue()
            if len(data) > config.MAX_FILE_MB * 1024 * 1024:
                raise CVReadError(f"Файл більший за {config.MAX_FILE_MB} МБ.")
            cv = load_cv(uploaded.name, data)
        else:
            cv = CVFile(filename="demo_cv.txt", text=DEMO_CV_TEXT)
        with st.spinner("Аналізую CV, це займає до хвилини..."):
            s.analysis = analyze_cv(get_client(), profile, cv)
        s.cv, s.profile = cv, profile
        s.grill, s.grill_result = None, None
        for k in [k for k in st.session_state if str(k).startswith("accept_")]:
            del st.session_state[k]
    except (CVReadError, LLMError) as e:
        st.error(str(e))
if not target_role.strip():
    st.caption("Щоб почати, вкажи роль.")

if s.analysis is None:
    st.stop()

# ---------- Результати ----------
a = s.analysis
st.header("3. Результат")
tab_overview, tab_edits, tab_gaps, tab_grill, tab_export = st.tabs(
    ["Огляд", "Правки", "Що вивчити", "Grill me", "Готове CV"]
)

with tab_overview:
    st.metric("Готовність CV під ціль", f"{a.overall_score}/100")
    st.write(a.summary)
    with st.expander("Як я зрозумів твою ціль (виправ у формі, якщо не так)"):
        for t in a.target_assumptions:
            st.write(f"- {t}")
    st.subheader("Оцінки за критеріями")
    for c in a.scores:
        st.write(f"**{c.criterion}**: {'●' * c.score}{'○' * (5 - c.score)}  {c.comment}")
    if a.strengths:
        st.subheader("Що вже добре")
        for x in a.strengths:
            st.write(f"- {x}")

with tab_edits:
    st.caption("Відміть правки, які приймаєш. Числа в [дужках] заміни на свої.")
    for i, e in enumerate(all_edits(s)):
        with st.container(border=True):
            st.markdown(f"**{e.section}** · {PRIORITY_LABEL[e.priority]}")
            c1, c2 = st.columns(2)
            c1.markdown("**Було**")
            c1.write(e.before or "_(новий пункт)_")
            c2.markdown("**Стало**")
            c2.write(e.after or "_(прибрати)_")
            st.caption(e.reason)
            st.checkbox("Приймаю", key=f"accept_{i}")

with tab_gaps:
    st.caption("Що зробити поза CV, щоб сильно підняти шанси. Від найважливішого.")
    for g in a.gaps:
        with st.container(border=True):
            st.markdown(f"**{g.item}** · {PRIORITY_LABEL[g.impact]} · {g.time_estimate}")
            st.write(g.why_it_matters)
            st.write(f"**Як:** {g.how_to_close}")

with tab_grill:
    st.caption(
        f"Я поставлю до {config.GRILL_MAX_QUESTIONS} питань про твій досвід. "
        "Відповіді допоможуть написати сильніші пункти. Я нічого не вигадую, тільки те, що ти скажеш."
    )
    if s.grill is None:
        if st.button("Почати Grill me"):
            s.grill = GrillSession()
            try:
                with st.spinner("Думаю над першим питанням..."):
                    next_question(get_client(), s.profile, s.cv, s.grill)
            except LLMError as e:
                st.error(str(e))
            st.rerun()
    else:
        g = s.grill
        for i, t in enumerate(g.turns, 1):
            if g.finished and not t.answer:
                continue
            with st.chat_message("assistant"):
                st.write(f"**{i}.** {t.question}")
                st.caption(t.why_asking)
            if t.answer:
                with st.chat_message("user"):
                    st.write(t.answer)
        if g.pending is not None:
            with st.form("grill_answer", clear_on_submit=True):
                reply = st.text_area("Твоя відповідь")
                c1, c2 = st.columns(2)
                send = c1.form_submit_button("Відповісти", type="primary")
                skip = c2.form_submit_button("Пропустити")
            if send or skip:
                answer(g, "" if skip else reply)
                try:
                    with st.spinner("Наступне питання..."):
                        next_question(get_client(), s.profile, s.cv, g)
                except LLMError as e:
                    st.error(str(e))
                st.rerun()
        if s.grill_result is None and any(t.answer for t in g.turns):
            if st.button("Завершити і отримати правки", type="primary" if g.pending is None else "secondary"):
                try:
                    with st.spinner("Перетворюю відповіді на правки..."):
                        s.grill_result = finalize(get_client(), s.profile, s.cv, g)
                    st.rerun()
                except LLMError as e:
                    st.error(str(e))
        if s.grill_result is not None:
            st.success(f"Готово: {len(s.grill_result.edits)} нових правок додано у вкладку «Правки».")

with tab_export:
    edits = all_edits(s)
    accepted = [e for i, e in enumerate(edits) if st.session_state.get(f"accept_{i}")]
    if not accepted:
        st.info("Спершу прийми хоча б одну правку у вкладці «Правки».")
    elif not s.cv.text.strip():
        st.warning("З цього PDF не вдалося витягти текст (схоже на скан). Бери правки зі списку нижче.")
        st.download_button("Завантажити список правок (.md)", changes_markdown(accepted), "cvmax_changes.md")
    else:
        report = apply_edits(s.cv.text, accepted)
        st.caption(
            "Це текстова версія CV з твоїми правками. Перенеси її у свій шаблон, "
            "бо оформлення оригіналу тут не зберігається."
        )
        st.text_area("CV з правками", report.text, height=400)
        if report.not_found:
            st.warning(
                f"{len(report.not_found)} правок не вдалося знайти в тексті автоматично. "
                "Внеси їх вручну, вони є у списку правок."
            )
        c1, c2, c3 = st.columns(3)
        c1.download_button("CV (.docx)", text_to_docx(report.text), "cv_cvmax.docx")
        c2.download_button("CV (.txt)", report.text, "cv_cvmax.txt")
        c3.download_button("Список правок (.md)", changes_markdown(accepted), "cvmax_changes.md")
