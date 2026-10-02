"""Feedback: what you liked, what broke, what's missing."""

import streamlit as st

from ui.account import send_feedback
from ui.common import card

st.title("Feedback")
st.markdown(
    '<p class="cvx-page-lead">Your feedback decides what we fix first. '
    "Most useful of all: where CVmax got it wrong, gave bad advice or made something up.</p>",
    unsafe_allow_html=True,
)
with card("feedback-form"):
    with st.form("feedback", clear_on_submit=True, border=False):
        topic = st.segmented_control(
            "What is it about?", ["CV review", "Q&A", "Final CV", "Where to apply", "CV builder", "Other"],
            default="CV review",
        )
        rating = st.feedback("thumbs")
        message = st.text_area(
            "What happened, or what should we improve?", max_chars=2000, height=160,
            placeholder="E.g.: it told me to cut a line that matters for this job; or: it couldn't read my PDF.",
        )
        sent = st.form_submit_button("Send", type="primary")
    if sent:
        if not message.strip() and rating is None:
            st.warning("Leave a rating or write a few words.")
        elif send_feedback(f"feedback:{topic or 'Other'}", rating, message):
            st.success("Thank you! We read every message.")
st.caption(
    "Feedback is anonymous: we don't store your email with it. Don't include your phone number or other personal details."
)
