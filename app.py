"""CVMAX: вебзастосунок. Запуск: streamlit run app.py"""

import streamlit as st

st.set_page_config(page_title="CVMAX", page_icon="📄", layout="centered")

pages = {
    "": [
        st.Page("views/home.py", title="Головна", icon=":material/home:", default=True),
        st.Page("views/analyze.py", title="Аналіз CV", icon=":material/description:"),
        st.Page("views/career.py", title="Куди податись", icon=":material/explore:"),
        st.Page("views/builder.py", title="Конструктор CV", icon=":material/edit_note:"),
    ],
    "Про CVMAX": [
        st.Page("views/about.py", title="Про нас", icon=":material/info:"),
        st.Page("views/privacy.py", title="Конфіденційність", icon=":material/shield:"),
        st.Page("views/terms.py", title="Умови", icon=":material/gavel:"),
    ],
}
st.logo("assets/logo.svg", size="large")
st.navigation(pages, position="top").run()
