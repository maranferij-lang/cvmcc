"""Мій кабінет: профіль, збережені результати, видалення даних, вихід."""

import streamlit as st

from cvmax.cv_render import render_docx, render_markdown
from cvmax.db import DBError
from cvmax.profile import PROGRAMS, STATUSES
from cvmax.schemas import Analysis, BuiltCV, CareerMatch
from ui.account import current_email, get_db, save_profile, user_row

row = user_row() or {}
email = current_email()
st.title("Мій кабінет")
st.caption(f"Ти увійшов(-ла) як {email}")

KIND_LABEL = {"analysis": "Аналіз CV", "career": "Куди податись", "builder": "Конструктор"}

tab_history, tab_profile, tab_data = st.tabs(["Мої результати", "Профіль", "Дані й вихід"])

with tab_history:
    if not get_db().persistent:
        st.info("Збереження результатів ще не налаштоване на цьому сайті.")
    else:
        try:
            items = get_db().list_results(email)
        except DBError as e:
            st.error(str(e))
            items = []
        if not items:
            st.write("Тут з'являться твої аналізи, напрями і зібрані CV.")
        for item in items:
            label = f"{KIND_LABEL.get(item['kind'], item['kind'])} · {item['title']} · {item['created_at'][:10]}"
            with st.expander(label):
                if not st.toggle("Показати", key=f"show_{item['id']}"):
                    continue
                full = get_db().get_result(email, item["id"]) or {}
                payload = full.get("payload") or {}
                if item["kind"] == "analysis" and "analysis" in payload:
                    a = Analysis.model_validate(payload["analysis"])
                    st.metric("Готовність CV", f"{a.overall_score}/100")
                    st.write(a.summary)
                    for e in a.edits:
                        st.markdown(f"- **{e.section}:** {e.after or '(прибрати)'}")
                elif item["kind"] == "career" and "career" in payload:
                    c = CareerMatch.model_validate(payload["career"])
                    st.write(c.general_advice)
                    for d in c.directions:
                        st.markdown(f"- **{d.role}** ({d.company_type}): {d.fit_score}%")
                elif item["kind"] == "builder" and "cv" in payload:
                    cv = BuiltCV.model_validate(payload["cv"])
                    st.markdown(render_markdown(cv))
                    st.download_button("Завантажити DOCX", render_docx(cv), f"cv_{cv.full_name}.docx",
                                       key=f"dl_{item['id']}")

with tab_profile:
    with st.form("profile"):
        programs = list(PROGRAMS)
        program = st.selectbox("Програма в КШЕ", programs, format_func=PROGRAMS.get,
                               index=programs.index(row["program"]) if row.get("program") in programs else 0)
        status = st.selectbox("Статус", STATUSES,
                              index=STATUSES.index(row["status"]) if row.get("status") in STATUSES else 0)
        background = st.text_area("Твій досвід коротко", value=row.get("background") or "", height=80)
        goal = st.text_input("Чого хочеш досягти?", value=row.get("goal") or "")
        if st.form_submit_button("Зберегти", type="primary"):
            try:
                save_profile(program, status, background, goal)
                st.success("Збережено.")
            except DBError as e:
                st.error(str(e))

with tab_data:
    st.write("Ти можеш будь-коли видалити всі свої дані: профіль, збережені результати й історію використання.")
    confirm = st.checkbox("Так, я хочу назавжди видалити свої дані")
    if st.button("Видалити мої дані", disabled=not confirm):
        try:
            get_db().delete_my_data(email)
            st.session_state.pop("user_row", None)
            st.success("Дані видалено.")
            st.logout()
        except DBError as e:
            st.error(str(e))
    st.divider()
    st.button("Вийти", icon=":material/logout:", on_click=st.logout)
