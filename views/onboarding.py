"""Онбординг після першого входу: знайомство з платформою і короткий профіль."""

import streamlit as st

from cvmax.profile import PROGRAMS, STATUSES
from cvmax.db import DBError
from ui.account import save_profile, user_row
from ui.common import inject_css

inject_css()
row = user_row() or {}
first_name = (row.get("name") or "").split(" ")[0]

st.markdown(
    f"""
<div class="cvx-hero">
  <span class="cvx-badge">Крок 1 з 1</span>
  <h1>Вітаємо в CVMAX{', ' + first_name if first_name else ''}!</h1>
  <p>Розкажи трохи про себе. Це займе хвилину, і ми підставлятимемо ці дані в усі інструменти,
  щоб поради були точнішими.</p>
</div>
""",
    unsafe_allow_html=True,
)

c1, c2, c3 = st.columns(3)
c1.markdown("**Аналіз CV**  \nПравки під конкретну вакансію і план, що вивчити.")
c2.markdown("**Куди податись**  \nНапрями, де з твоїм CV найбільше шансів.")
c3.markdown("**Конструктор**  \nПерше CV з нуля через коротке інтерв'ю.")

with st.form("onboarding"):
    col1, col2 = st.columns(2)
    program = col1.selectbox("Програма в КШЕ", list(PROGRAMS), format_func=PROGRAMS.get)
    status = col2.selectbox("Статус", STATUSES)
    background = st.text_area(
        "Твій досвід коротко", placeholder="Напр.: стажування в продажах, волонтер у студраді, курс з Python",
        height=80,
    )
    goal = st.text_input("Чого хочеш досягти?", placeholder="Напр.: літнє стажування в консалтингу або аналітиці")
    submitted = st.form_submit_button("Почати користуватись", type="primary")

if submitted:
    try:
        save_profile(program, status, background, goal)
    except DBError as e:
        st.error(f"Не вдалося зберегти профіль: {e}")
        st.stop()
    st.rerun()
