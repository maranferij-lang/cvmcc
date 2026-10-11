"""Terms of use."""

import streamlit as st

from cvmax import config
from ui.account import auth_configured, user_limit_text
from ui.common import secret

st.title("Terms")
st.caption("Last updated: October 2, 2026")
st.markdown(
    '<p class="cvx-page-lead">In short: GetCVmax is free and gives advice, but you decide what goes in your CV. '
    "Only write the truth.</p>",
    unsafe_allow_html=True,
)

limits = config.LIMITS
sections = [
    ("What GetCVmax is",
     "GetCVmax is a free experimental tool that uses AI to give advice on CVs and career directions. "
     "It is an independent project."),
    ("No guarantees",
     "The advice comes from a language model, and it can make mistakes. GetCVmax does not guarantee interviews "
     "or job offers. The service is provided \"as is\" and may be unavailable, change or shut down at any time."),
    ("Your responsibility",
     "You decide which edits to accept. Check each one and put only the truth in your CV. "
     "If an edit contains a fact that isn't true for you, remove it. Replace placeholders like [X] with "
     "real numbers or remove them."),
    ("Acceptable use",
     "Don't upload someone else's CV without their consent, files with malicious content, or data unrelated "
     "to a CV. Don't try to bypass the limits, access other people's data, overload the service or get the "
     "model to do anything other than help with CVs. We may block access if you do."),
    ("Account and limits",
     ("Sign-in is through Google. " if auth_configured() else "No sign-up needed. ")
     + "To keep the service free, the number of actions per day is limited: "
     f"{user_limit_text('analysis')}, {user_limit_text('career')}, "
     f"{user_limit_text('grill')}, {user_limit_text('builder')}, "
     f"{user_limit_text('build')} and {user_limit_text('export')}. "
     + ("There is also an overall daily limit for the whole site. " if any(v[1] is not None for v in limits.values())
        else "")
     + "Limits reset every day."),
    ("Your content",
     "Your CV and answers remain yours. You allow us to process them only to show you results, and to store "
     "the text of edits to improve our advice. You can use your final CV however you like."),
    ("Data",
     "How we handle your data is described on the Privacy page."
     + (" You can delete your data in My account." if auth_configured() else "")),
    ("Changes",
     "We may update these terms. The current version is always on this page."),
]
for title, text in sections:
    st.subheader(title)
    st.write(text)
contact = secret("CVMAX_CONTACT")
if contact:
    st.subheader("Contact")
    st.write(contact)
