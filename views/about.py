"""About."""

import streamlit as st

from cvmax import config
from ui.common import card, secret

st.title("About CVmax")
st.markdown(
    '<p class="cvx-page-lead">CVmax is a free AI CV coach for students and early-career people applying to '
    "international companies. It helps you build an English CV that reads in six seconds and tells a recruiter "
    "why you are worth a call. No generic tips, no invented experience.</p>",
    unsafe_allow_html=True,
)
st.markdown(
    f"""
<div class="cvx-facts">
  <div><b>6</b><span>fields of study with their own criteria</span></div>
  <div><b>47</b><span>sources behind the criteria: recruiters, career centers, research</span></div>
  <div><b>{config.GRILL_MAX_QUESTIONS}</b><span>questions in the Q&amp;A to find experience missing from your CV</span></div>
</div>
""",
    unsafe_allow_html=True,
)

st.header("Why")
st.write(
    "You need a strong English CV for internships and your first job, but writing one is hard. "
    "There is plenty of advice online, and most of it contradicts itself. Career advisors can't sit with "
    "everyone over every job posting. And general chatbots are happy to rewrite your CV in polished words "
    "a recruiter won't believe, because there are no facts behind them."
)
st.write(
    "CVmax was built by a student who went through rewriting their own CV many times: hunting for examples, "
    "editing line by line, and only then learning that what matters isn't the wording. It's what to keep, "
    "what to cut and how to show results."
)

st.header("How CVmax thinks")
steps = [
    ("Goal first, then the CV",
     "The same line can be strong for consulting and useless for software. So every review starts with the "
     "role, the type of company and, ideally, the full job posting."),
    ("Every line on its own",
     "The model gives each line a verdict: keep, shorten, rewrite, move or cut. A bullet that names a topic "
     "with no action or result doesn't stay. Neither do duplicates or bragging that doesn't serve your goal."),
    ("One page",
     "We count words and pages. If your CV runs long, CVmax tells you what to cut first, based on your goal."),
    ("Never makes things up",
     "Where a number is missing, you get [X], not an invented figure. A separate checker flags skills, numbers "
     "and links in the edits that appear neither in your CV nor in your answers."),
    ("Asks instead of guessing",
     "The Q&A alternates two kinds of questions: pinning down numbers in your existing bullets and finding "
     "experience you left out: projects, AI tools, research, volunteering."),
    ("Learns from feedback",
     "We see which edits people accept and which they reject, and we adjust the criteria. Every mistake we "
     "find goes into our test set so it doesn't happen again."),
]
for row in range(0, len(steps), 2):
    for col, (i, (title, text)) in zip(st.columns(2), enumerate(steps[row:row + 2], start=row)):
        with col, card(f"how-{i}"):
            st.markdown(f"**{title}**")
            st.write(text)

st.header("What's inside")
st.markdown(
    "- **Model:** Google Gemini (free tier).\n"
    "- **Criteria:** general rules for a strong CV plus separate ones for each field, built from advice by "
    "recruiters at consulting and tech companies, university career centers and research on how CVs are read.\n"
    "- **Quality checks:** a set of made-up CVs with known problems that we run every change against."
)

st.header("Status and feedback")
st.write(
    "CVmax is free. Daily limits keep it free for everyone. The most useful thing you can send us: cases where "
    "CVmax got it wrong. Tell us what happened and we'll add it to our tests."
)
st.page_link("views/feedback.py", label="Leave feedback", icon=":material/chat:")
contact = secret("CVMAX_CONTACT")
if contact:
    st.write(f"Contact us: {contact}")
