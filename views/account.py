"""Мій кабінет: профіль, збережені результати, видалення даних, вихід."""

from datetime import datetime, timezone

import streamlit as st

from cvmax.cv_render import render_docx, render_markdown, render_pdf
from cvmax.db import DBError
from cvmax.profile import PROGRAMS, STATUSES
from cvmax.safe_text import md_escape
from cvmax.schemas import Analysis, BuiltCV, CareerMatch
from ui.account import current_email, get_db, log_event, save_profile, user_row

row = user_row() or {}
email = current_email()
st.title("My account")
st.caption(f"Signed in as {email}")

OUTCOMES = {"Yes": "yes", "No": "no", "Not yet": "not_yet"}


def older_than_week(created_at: str) -> bool:
    try:
        dt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).days >= 7


def result_analysis_id(item: dict) -> str:
    """analysis_id збереженого результату (потрібен для нагороди варіанта); порожній для старих результатів."""
    if "analysis_id" in item:  # list_results уже віддає його, додаткових запитів не треба
        return str(item["analysis_id"] or "")
    key = f"analysis_id_{item['id']}"
    if key not in st.session_state:
        try:
            full = get_db().get_result(email, item["id"]) or {}
            st.session_state[key] = str((full.get("payload") or {}).get("analysis_id") or "")
        except DBError:
            st.session_state[key] = ""  # кешуємо й збій, щоб не повторювати запит на кожен rerun
    return st.session_state[key]


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
                aid = result_analysis_id(item) if item["kind"] == "analysis" and older_than_week(item["created_at"]) else ""
                if aid:
                    value = st.segmented_control("Did this CV get you an interview?", list(OUTCOMES),
                                                 key=f"outcome_{item['id']}")
                    if value and st.session_state.get(f"outcome_sent_{item['id']}") != value:
                        log_event("outcome", result_id=item["id"], analysis_id=aid, answer=OUTCOMES[value])
                        st.session_state[f"outcome_sent_{item['id']}"] = value
                    if value:
                        st.caption("Thanks, this helps GetCVmax learn.")
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
