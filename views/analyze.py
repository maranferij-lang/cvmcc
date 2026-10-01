"""Сторінка «Аналіз CV»: правки під вакансію, план навчання, Grill me, готове CV."""

from __future__ import annotations

import uuid

import streamlit as st

from cvmax import config
from cvmax.analyze import analyze_cv
from cvmax.cv_render import has_placeholders, pdf_preview, render_docx, render_pdf
from cvmax.edits import apply_edits, changes_markdown, lost_facts, unverified_terms
from cvmax.grill import GrillSession, answer, finalize, next_question
from cvmax.llm import LLMError
from cvmax.profile import COMPANY_TYPES, FEEDBACK_LANGUAGES, LEVELS, PROGRAMS, REGIONS, STATUSES, Profile
from cvmax.safe_text import md_escape
from cvmax.structure import changed_bullets, structure_cv
from ui.account import log_edit_feedback, profile_value, require_login, save_result, send_feedback, take_limit
from ui.common import BOT_AVATAR, card, consent, cv_picker, demo_banner, get_llm

PRIORITY_LABEL = {"high": ":red-badge[Важливо]", "medium": ":orange-badge[Бажано]", "low": ":gray-badge[Дрібниця]"}


def get_client():
    return get_llm()


def state():
    s = st.session_state
    s.setdefault("analysis", None)
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
    """Якщо дві прийняті правки змінюють той самий рядок, лишаємо пізнішу: Grill me знає більше фактів."""
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


def send_feedback(s) -> None:
    """Один раз на аналіз (і ще раз, якщо змінився вибір): які правки прийнято, а які ні."""
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


# ---------- Шапка ----------
require_login("Аналіз CV")
s = state()
# Значення з профілю підставляються в форму один раз за сесію.
if not s.get("analyze_defaults_set"):
    s["analyze_defaults_set"] = True
    if profile_value("program") in PROGRAMS:
        s["analyze_program"] = profile_value("program")
    if profile_value("status") in STATUSES:
        s["analyze_status"] = profile_value("status")
    s["analyze_background"] = profile_value("background", "")
# Перехід зі сторінки «Куди податись»: підставляємо обраний напрям у форму.
if "prefill_role" in st.session_state:
    st.session_state["target_role"] = st.session_state.pop("prefill_role")
    st.session_state["company_type"] = st.session_state.pop("prefill_company")

st.title("Аналіз CV")
st.caption("Правки під конкретну роль, план навчання і Grill me.")
demo_banner()
if not consent("analyze"):
    st.stop()

# ---------- Онбординг ----------
with card("goal"):
    st.header("1. Про тебе і твою ціль")
    col1, col2 = st.columns(2)
    program = col1.selectbox("Програма в КШЕ", list(PROGRAMS), format_func=PROGRAMS.get, key="analyze_program")
    status = col2.selectbox("Статус", STATUSES, key="analyze_status")
    background = st.text_area(
        "Де ти зараз вчишся, працюєш або працював(-ла)?",
        max_chars=1500,
        key="analyze_background",
        placeholder="Напр.: 3 курс, літнє стажування в продажах, волонтер у студраді",
        height=80,
    )

    st.subheader("Куди хочеш потрапити")
    target_role = st.text_input("Роль", key="target_role", max_chars=120, placeholder="Напр.: Business Analyst, Junior Data Analyst, UX Researcher")
    col1, col2 = st.columns(2)
    company_type = col1.selectbox("Тип компанії", COMPANY_TYPES, key="company_type")
    level = col2.selectbox("Рівень", LEVELS)
    company_details = st.text_input(
        "Конкретна компанія або індустрія (необов'язково)", placeholder="Напр.: Monobank, McKinsey, EdTech-стартап",
        max_chars=200,
    )
    region = st.selectbox("Ринок", REGIONS)
    vacancy_text = st.text_area(
        "Текст вакансії (дуже бажано)",
        max_chars=8000,
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

with card("cv"):
    st.header("2. Твоє CV")
    cv = cv_picker("analyze")

can_run = bool(target_role.strip()) and cv is not None
if st.button("Проаналізувати CV", type="primary", disabled=not can_run) and take_limit("analysis"):
    try:
        with st.spinner("Аналізую CV, це займає до хвилини..."):
            s.analysis = analyze_cv(get_client(), profile, cv)
        s.cv, s.profile = cv, profile
        s.analysis_id = uuid.uuid4().hex
        s.pop("feedback_sent", None)
        s.pop("formatted_cv", None)
        save_result("analysis", f"{target_role} · {s.analysis.overall_score}/100",
                    {"role": target_role, "company_type": company_type, "analysis": s.analysis.model_dump()})
        s.grill, s.grill_result = None, None
        for k in [k for k in st.session_state if str(k).startswith("accept_")]:
            del st.session_state[k]
    except LLMError as e:
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
    st.write(md_escape(a.summary))
    with st.expander("Як я зрозумів твою ціль (виправ у формі, якщо не так)"):
        for t in a.target_assumptions:
            st.markdown(f"- {md_escape(t)}")
    st.subheader("Оцінки за критеріями")
    for c in a.scores:
        st.markdown(f"**{md_escape(c.criterion)}**: {'●' * c.score}{'○' * (5 - c.score)}  {md_escape(c.comment)}")
    flagged = [v for v in a.line_review if v.verdict != "keep"]
    if a.line_review:
        st.subheader("Перевірка кожного рядка")
        st.caption(
            f"Переглянуто {len(a.line_review)} рядків, до {len(flagged)} є зауваження. "
            "Відповідні правки у вкладці «Правки»."
        )
        verdict_label = {"cut": ":red-badge[Прибрати]", "shorten": ":orange-badge[Скоротити]",
                         "rewrite": ":blue-badge[Переписати]", "move": ":violet-badge[Перенести]"}
        for v in flagged:
            st.markdown(f"- {verdict_label.get(v.verdict, v.verdict)} {md_escape(v.line)}  \n  {md_escape(v.reason)}")
    if a.strengths:
        st.subheader("Що вже добре")
        for x in a.strengths:
            st.markdown(f"- {md_escape(x)}")
    with card("rate-analysis"):
        st.markdown("**Чи корисний цей аналіз?**")
        rated = st.feedback("thumbs", key=f"rate_{s.analysis_id}")
        if rated is not None and s.get("rated_analysis") != s.analysis_id:
            s["rated_analysis"] = s.analysis_id
            if send_feedback("analysis", rated, f"{s.profile.program} · {s.profile.target_role}"[:200]):
                st.toast("Дякуємо за оцінку!")
        st.page_link("views/feedback.py", label="Розповісти детальніше", icon=":material/chat:")

def known_facts(s) -> str:
    """Усе, що юзер сам про себе сказав: CV, онбординг і відповіді в Grill me."""
    parts = [s.cv.text, s.profile.background]
    if s.grill:
        parts += [t.answer for t in s.grill.turns]
    return "\n".join(parts)


with tab_edits:
    st.caption(
        "Відміть правки, які приймаєш. Усе в [дужках] заміни на своє або прибери, якщо це неправда."
    )
    facts = known_facts(s)
    for i, e in enumerate(all_edits(s)):
        with card(f"edit-{i}"):
            st.markdown(f"**{md_escape(e.section)}** · {PRIORITY_LABEL[e.priority]}")
            c1, c2 = st.columns(2)
            with c1.container(key=f"before-{i}"):
                st.caption("БУЛО")
                st.markdown(md_escape(e.before) or "_(новий пункт)_")
            with c2.container(key=f"after-{i}"):
                st.caption("СТАЛО")
                st.markdown(md_escape(e.after) or "_(прибрати)_")
            st.caption(md_escape(e.reason))
            dropped = lost_facts(e.before, e.after)
            if dropped:
                st.warning(
                    "Ця правка прибирає з пункту факти: " + ", ".join(dropped)
                    + ". Якщо вони правдиві й важливі, краще не приймай її або допиши їх у текст."
                )
            flagged = unverified_terms(e.after, facts)
            if flagged:
                st.warning(
                    "Цього немає ні в CV, ні у твоїх відповідях: " + ", ".join(flagged)
                    + ". Залиш тільки те, що правда."
                )
            st.checkbox("Приймаю", key=f"accept_{i}")

with tab_gaps:
    st.caption("Що зробити поза CV, щоб сильно підняти шанси. Від найважливішого.")
    for j, g in enumerate(a.gaps):
        with card(f"gap-{j}"):
            st.markdown(f"**{md_escape(g.item)}** · {PRIORITY_LABEL[g.impact]} · {md_escape(g.time_estimate)}")
            st.markdown(md_escape(g.why_it_matters))
            st.markdown(f"**Як:** {md_escape(g.how_to_close)}")

with tab_grill:
    st.caption(
        f"Я поставлю до {config.GRILL_MAX_QUESTIONS} питань про твій досвід. "
        "Відповіді допоможуть написати сильніші пункти. Я нічого не вигадую, тільки те, що ти скажеш."
    )
    if s.grill is None:
        if st.button("Почати Grill me") and take_limit("grill"):
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
            with st.chat_message("assistant", avatar=BOT_AVATAR):
                st.markdown(f"**{i}.** {md_escape(t.question)}")
                st.caption(md_escape(t.why_asking))
            if t.answer:
                with st.chat_message("user"):
                    st.markdown(md_escape(t.answer))
        if g.pending is not None:
            with st.form("grill_answer", clear_on_submit=True):
                reply = st.text_area("Твоя відповідь", max_chars=1500)
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
    accepted = latest_per_line([e for i, e in enumerate(edits) if st.session_state.get(f"accept_{i}")])
    if not accepted:
        st.info("Спершу прийми хоча б одну правку у вкладці «Правки».")
    elif not s.cv.text.strip():
        st.warning("З цього PDF не вдалося витягти текст (схоже на скан). Бери правки зі списку нижче.")
        st.download_button("Завантажити список правок (.md)", changes_markdown(accepted), "cvmax_changes.md",
                           on_click=send_feedback, args=(s,))
    else:
        report = apply_edits(s.cv.text, accepted)
        if report.not_found:
            st.warning(
                f"{len(report.not_found)} правок не вдалося знайти в тексті автоматично. "
                "Внеси їх вручну, вони є у списку правок."
            )
        with st.expander("Текст CV з правками", expanded=False):
            text = st.text_area("Можна підправити перед оформленням", report.text, height=400, max_chars=40_000,
                                key=f"export_text_{hash(report.text)}")

        st.subheader("Оформлене CV")
        st.caption("Один шаблон, перевірений рекрутерами: шрифт без засічок, чіткі розділи, дати праворуч.")
        formatted = s.get("formatted_cv")
        if formatted is None or formatted[0] != text:
            if st.button("Оформити CV (PDF і DOCX)", type="primary") and take_limit("export"):
                try:
                    with st.spinner("Розкладаю CV по розділах..."):
                        s["formatted_cv"] = (text, structure_cv(get_client(), text))
                    send_feedback(s)
                    st.rerun()
                except LLMError as e:
                    st.error(str(e))
        else:
            built = formatted[1]
            changed = changed_bullets(text, built)
            if changed:
                st.warning(
                    "Ці пункти в оформленому CV відрізняються від твого тексту. Перевір їх перед відправкою:\n\n"
                    + "\n".join(f"- {b}" for b in changed)
                )
            holes = has_placeholders(built)
            if holes:
                st.warning("Заміни або прибери заповнювачі в цих рядках:\n\n" + "\n".join(f"- {h}" for h in holes))
            pdf = render_pdf(built)
            with card("export-preview"):
                for page in pdf_preview(pdf):
                    st.image(page, width="stretch")
            name = built.full_name.replace(" ", "_") or "cv"
            c1, c2 = st.columns(2)
            c1.download_button("Завантажити PDF", pdf, f"{name}_CV.pdf", mime="application/pdf",
                               type="primary", on_click=send_feedback, args=(s,))
            c2.download_button("Завантажити DOCX", render_docx(built), f"{name}_CV.docx",
                               on_click=send_feedback, args=(s,))
            st.caption("DOCX можна відкрити у Word або Google Docs, якщо хочеш щось змінити вручну.")

        st.divider()
        c1, c2 = st.columns(2)
        c1.download_button("Текст CV (.txt)", text, "cv_cvmax.txt", on_click=send_feedback, args=(s,))
        c2.download_button("Список правок (.md)", changes_markdown(accepted), "cvmax_changes.md",
                           on_click=send_feedback, args=(s,))
