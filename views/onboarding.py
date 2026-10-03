"""Онбординг після першого входу: знайомство з платформою і короткий профіль."""

import html

import streamlit as st

from cvmax.profile import PROGRAMS, STATUSES
from cvmax.db import DBError
from ui.account import save_profile, user_row

row = user_row() or {}
first_name = html.escape((row.get("name") or "").split(" ")[0])

st.markdown(
    f"""
<div class="cvx-hero">
  <span class="cvx-eyebrow"><i></i>One minute</span>
  <h1>Welcome to GetCVmax{', ' + first_name if first_name else ''}!</h1>
  <p class="lead">Tell us a bit about yourself. It takes a minute, and we'll use it across all the tools
  to make the advice more accurate.</p>
</div>
""",
    unsafe_allow_html=True,
)

c1, c2, c3 = st.columns(3)
c1.markdown("**CV review**  \nEdits tailored to a specific job and a plan for what to learn.")
c2.markdown("**Where to apply**  \nDirections where your CV has the best chances.")
c3.markdown("**CV builder**  \nYour first CV from scratch through a short interview.")

with st.form("onboarding"):
    col1, col2 = st.columns(2)
    program = col1.selectbox("Field of study", list(PROGRAMS), format_func=PROGRAMS.get)
    status = col2.selectbox("Status", STATUSES)
    background = st.text_area(
        "Your experience in brief", placeholder="E.g.: sales internship, student council volunteer, Python course",
        max_chars=1500,
        height=80,
    )
    goal = st.text_input("What do you want to achieve?", max_chars=300, placeholder="E.g.: a summer internship in consulting or analytics")
    submitted = st.form_submit_button("Get started", type="primary")

if submitted:
    try:
        save_profile(program, status, background, goal)
    except DBError as e:
        st.error(f"Couldn't save your profile: {e}")
        st.stop()
    st.rerun()
