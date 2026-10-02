"""Мій кабінет: профіль, збережені результати, видалення даних, вихід."""

import streamlit as st

from cvmax.cv_render import render_docx, render_markdown, render_pdf
from cvmax.db import DBError
from cvmax.profile import PROGRAMS, STATUSES
from cvmax.safe_text import md_escape
from cvmax.schemas import Analysis, BuiltCV, CareerMatch
from ui.account import current_email, get_db, save_profile, user_row

row = user_row() or {}
email = current_email()
st.title("My account")
st.caption(f"Signed in as {email}")

KIND_LABEL = {"analysis": "CV review", "career": "Where to apply", "builder": "CV builder"}

tab_history, tab_profile, tab_data = st.tabs(["My results", "Profile", "Data and sign out"])

with tab_history:
    if not get_db().persistent:
        st.info("Saving results isn't set up on this site yet.")
    else:
        try:
            items = get_db().list_results(email)
        except DBError as e:
            st.error(str(e))
            items = []
        if not items:
            st.write("Your CV reviews, career directions and built CVs will show up here.")
        for item in items:
            label = f"{KIND_LABEL.get(item['kind'], item['kind'])} · {md_escape(item['title'])} · {item['created_at'][:10]}"
            with st.expander(label):
                if not st.toggle("Show", key=f"show_{item['id']}"):
                    continue
                try:
                    full = get_db().get_result(email, item["id"]) or {}
                except DBError as e:
                    st.error(str(e))
                    continue
                payload = full.get("payload") or {}
                if item["kind"] == "analysis" and "analysis" in payload:
                    payload["analysis"].setdefault("line_review", [])  # старі результати без перевірки рядків
                    a = Analysis.model_validate(payload["analysis"])
                    st.metric("CV fit", f"{a.overall_score}/100")
                    st.markdown(md_escape(a.summary))
                    for e in a.edits:
                        st.markdown(f"- **{md_escape(e.section)}:** {md_escape(e.after) or '(remove)'}")
                elif item["kind"] == "career" and "career" in payload:
                    c = CareerMatch.model_validate(payload["career"])
                    st.markdown(md_escape(c.general_advice))
                    for d in c.directions:
                        st.markdown(f"- **{md_escape(d.role)}** ({md_escape(d.company_type)}): {d.fit_score}%")
                elif item["kind"] == "builder" and "cv" in payload:
                    cv = BuiltCV.model_validate(payload["cv"])
                    st.markdown(render_markdown(cv))
                    name = cv.full_name.replace(" ", "_") or "cv"
                    c1, c2 = st.columns(2)
                    c1.download_button("PDF", render_pdf(cv), f"{name}_CV.pdf", mime="application/pdf",
                                       key=f"pdf_{item['id']}")
                    c2.download_button("DOCX", render_docx(cv), f"{name}_CV.docx", key=f"dl_{item['id']}")

with tab_profile:
    with st.form("profile"):
        programs = list(PROGRAMS)
        program = st.selectbox("Field of study", programs, format_func=PROGRAMS.get,
                               index=programs.index(row["program"]) if row.get("program") in programs else 0)
        status = st.selectbox("Status", STATUSES,
                              index=STATUSES.index(row["status"]) if row.get("status") in STATUSES else 0)
        background = st.text_area("Your experience in brief", max_chars=1500, value=row.get("background") or "", height=80)
        goal = st.text_input("What do you want to achieve?", max_chars=300, value=row.get("goal") or "")
        if st.form_submit_button("Save", type="primary"):
            try:
                save_profile(program, status, background, goal)
                st.success("Saved.")
            except DBError as e:
                st.error(str(e))

with tab_data:
    st.write("You can delete all your data at any time: your profile, saved results and usage history.")
    confirm = st.checkbox("Yes, I want to permanently delete my data")
    if st.button("Delete my data", disabled=not confirm):
        try:
            get_db().delete_my_data(email)
            st.session_state.pop("user_row", None)
            st.success("Your data has been deleted.")
            st.logout()
        except DBError as e:
            st.error(str(e))
    st.divider()
    st.button("Sign out", icon=":material/logout:", on_click=st.logout)
