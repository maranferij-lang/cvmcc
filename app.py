"""GetCVmax: web app. Run: streamlit run app.py"""

import streamlit as st

from ui.account import auth_configured, is_logged_in, needs_onboarding
from ui.common import inject_css

st.set_page_config(page_title="GetCVmax: AI CV coach", page_icon="assets/icon.png", layout="wide")
st.logo("assets/logo.png", size="large")
inject_css()

home = st.Page("views/home.py", title="Home", icon=":material/home:", default=True)

if is_logged_in() and needs_onboarding():
    # After the first sign-in: a short introduction and the profile first.
    pages = {"": [st.Page("views/onboarding.py", title="Welcome", icon=":material/waving_hand:", default=True)]}
else:
    main = [
        home,
        st.Page("views/analyze.py", title="CV review", icon=":material/description:"),
        st.Page("views/career.py", title="Where to apply", icon=":material/explore:"),
        st.Page("views/builder.py", title="CV builder", icon=":material/edit_note:"),
    ]
    if is_logged_in():
        main.append(st.Page("views/account.py", title="My account", icon=":material/account_circle:"))
    elif auth_configured():
        main.append(st.Page("views/login.py", title="Sign in", icon=":material/login:"))
    pages = {
        "": main,
        "About": [
            st.Page("views/about.py", title="About GetCVmax", icon=":material/info:"),
            st.Page("views/privacy.py", title="Privacy", icon=":material/shield:"),
            st.Page("views/terms.py", title="Terms", icon=":material/gavel:"),
            st.Page("views/feedback.py", title="Feedback", icon=":material/chat:"),
        ],
    }

page = st.navigation(pages, position="top")
# Browser tab title: which page it is and whose site.
st.set_page_config(page_title="GetCVmax: free AI CV coach that tailors your CV to the job" if page.title == "Home" else f"{page.title} · GetCVmax")
page.run()
