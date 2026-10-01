"""CVmax: вебзастосунок. Запуск: streamlit run app.py"""

import streamlit as st

from ui.account import auth_configured, is_logged_in, needs_onboarding
from ui.common import inject_css

st.set_page_config(page_title="CVmax: CV під вакансію", page_icon="assets/icon.png", layout="wide")
st.logo("assets/logo.svg", size="large")
inject_css()

home = st.Page("views/home.py", title="Головна", icon=":material/home:", default=True)

if is_logged_in() and needs_onboarding():
    # Після першого входу: спершу коротке знайомство і профіль.
    pages = {"": [st.Page("views/onboarding.py", title="Ласкаво просимо", icon=":material/waving_hand:", default=True)]}
else:
    main = [
        home,
        st.Page("views/analyze.py", title="Аналіз CV", icon=":material/description:"),
        st.Page("views/career.py", title="Куди податись", icon=":material/explore:"),
        st.Page("views/builder.py", title="Конструктор CV", icon=":material/edit_note:"),
    ]
    if is_logged_in():
        main.append(st.Page("views/account.py", title="Мій кабінет", icon=":material/account_circle:"))
    elif auth_configured():
        main.append(st.Page("views/login.py", title="Увійти", icon=":material/login:"))
    pages = {
        "": main,
        "Про CVmax": [
            st.Page("views/about.py", title="Про нас", icon=":material/info:"),
            st.Page("views/privacy.py", title="Конфіденційність", icon=":material/shield:"),
            st.Page("views/terms.py", title="Умови", icon=":material/gavel:"),
            st.Page("views/feedback.py", title="Відгук", icon=":material/chat:"),
        ],
    }

st.navigation(pages, position="top").run()
