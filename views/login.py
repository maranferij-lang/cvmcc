"""Вхід через Google."""

import streamlit as st

from ui.common import card

st.title("Sign in to CVmax")
with card("login"):
    st.write(
        "Sign in with Google to use the tools. We'll save your profile and results "
        "so you can come back to them any time. You can delete all your data in My account."
    )
    st.button("Sign in with Google", type="primary", icon=":material/login:", on_click=st.login, args=("google",))
st.page_link("views/privacy.py", label="How we handle your data")
