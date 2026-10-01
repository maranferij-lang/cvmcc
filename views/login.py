"""Вхід через Google."""

import streamlit as st

from ui.common import card

st.title("Увійти в CVmax")
with card("login"):
    st.write(
        "Увійди через Google, щоб користуватись інструментами. Ми збережемо твій профіль і результати, "
        "і ти зможеш повернутись до них будь-коли. Видалити всі дані можна в «Мій кабінет»."
    )
    st.button("Увійти через Google", type="primary", icon=":material/login:", on_click=st.login, args=("google",))
st.page_link("views/privacy.py", label="Як ми обробляємо дані")
