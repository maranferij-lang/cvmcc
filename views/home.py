"""Home: the same story as getcvmax.com, with the app one click away."""

import re

import streamlit as st

from cvmax import config
from ui.account import auth_configured, join_waitlist

st.html('<div class="cvx-wide"></div>')  # wider page, see assets/style.css
ACCOUNTS = auth_configured()
_REVIEWS_PER_DAY = config.LIMITS["analysis"][0]  # None = no per-person limit; never print it as a number
MAIN_LIMIT = (
    "there is no daily limit on CV reviews per person" if _REVIEWS_PER_DAY is None
    else f"the main limit is {_REVIEWS_PER_DAY} CV reviews a day per person"
)
EMAIL = re.compile(r"^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,}$")

# ---------- Hero ----------
left, right = st.columns([1, 1.02], gap="large")
with left:
    st.markdown(
        """
<div class="cvx-hero">
  <span class="cvx-label">free while we launch</span>
  <h1>Your CV has <span class="cvx-mark">7&nbsp;seconds</span> to get you an interview.</h1>
  <p class="lead">Name your dream job. We read your CV like a recruiter in that field, mark every line keep,
  cut or rewrite, and tell you what to learn next to get there. Nothing made up: where a number is missing,
  we ask you.</p>
</div>
""",
        unsafe_allow_html=True,
    )
    c1, c2 = st.columns(2)
    if c1.button("Fix my CV", type="primary", icon=":material/arrow_forward:", width="stretch"):
        st.switch_page("views/analyze.py")
    if c2.button("No CV? Build one", width="stretch"):
        st.switch_page("views/builder.py")
    st.markdown(
        '<div class="cvx-next"><span><b>1</b> Name the job</span><span><b>2</b> Upload your PDF</span>'
        "<span><b>3</b> Results in about 2 minutes</span></div>",
        unsafe_allow_html=True,
    )

with right:
    st.markdown(
        """
<div class="cvx-sheet">
  <div class="cvx-stamp"><div><b>58</b><small>/100</small></div></div>
  <div class="name">MAYA CHEN</div>
  <div class="contact">London · maya.chen@email.com</div>
  <h5>Experience</h5>
  <div class="role"><b>Sales Intern, Northwind Retail</b><span>Jun – Aug 2025</span></div>
  <ul>
    <li><span class="del">Responsible for making reports in Excel</span>
      <span class="new">Built <span class="cvx-ph">N</span> weekly Excel reports on sales performance for the regional team</span>
      <span class="why">rewrite: say what you built and who used it</span></li>
    <li>Helped the team with client database<span class="why">keep: fine for an intern</span></li>
  </ul>
  <h5>Activities</h5>
  <ul>
    <li><span class="del">Member of Student Council</span>
      <span class="new">Organised 6 university events for 300+ students as Student Council member</span>
      <span class="why">you told us the numbers in Q&amp;A, so we used them</span></li>
  </ul>
  <h5>Personal</h5>
  <ul><li><span class="del">Date of birth: 01.01.2004</span><span class="why">cut: UK and US recruiters don't want it</span></li></ul>
</div>
<p class="cvx-note">Sample CV, made up for this page.</p>
""",
        unsafe_allow_html=True,
    )

# ---------- What it usually costs ----------
st.markdown(
    """
<div class="cvx-section">
  <span class="cvx-label">the usual way</span>
  <h2>What fixing a CV usually costs you</h2>
</div>
<div class="cvx-bill">
  <div class="r"><div><b>CV writer</b><span>3 to 7 days, and they don't know your field</span></div><div class="c">hundreds of dollars</div></div>
  <div class="r"><div><b>Career centre</b><span>1 to 2 weeks for a slot, 20 minutes per CV</span></div><div class="c">2 weeks</div></div>
  <div class="r"><div><b>Resume builder subscription</b><span>Templates and keyword scores, billed every month</span></div><div class="c">every month</div></div>
  <div class="r"><div><b>ChatGPT</b><span>Invents numbers you'll have to defend in the interview</span></div><div class="c">your credibility</div></div>
  <div class="t"><div>GetCVmax: every line checked, rewritten for the job</div><div class="c">free while we launch</div></div>
</div>
""",
    unsafe_allow_html=True,
)

# ---------- How it works ----------
st.markdown(
    f"""
<div class="cvx-section">
  <span class="cvx-label">how it works</span>
  <h2>Upload. Get the red pen. Download the fixed CV.</h2>
</div>
<div class="cvx-steps">
  <div class="cvx-step"><span class="n">1</span><div class="t">Upload your CV and the job ad</div>
    <div class="d">PDF or DOCX in English. Paste the job description, or just name the role.</div></div>
  <div class="cvx-step"><span class="n">2</span><div class="t">See every line marked</div>
    <div class="d">Keep, cut, rewrite or move, with the reason. Scored on 7 criteria recruiters use in your field.</div></div>
  <div class="cvx-step"><span class="n">3</span><div class="t">Answer up to {config.GRILL_MAX_QUESTIONS} questions</div>
    <div class="d">They dig out results you forgot to write. Then download a one-page PDF or DOCX that passes ATS.</div></div>
</div>
""",
    unsafe_allow_html=True,
)

# ---------- Comparison ----------
st.markdown(
    """
<div class="cvx-section">
  <span class="cvx-label">vs the alternatives</span>
  <h2>Why not just ask ChatGPT?</h2>
</div>
<div class="cvx-table"><table>
  <thead><tr><th></th><th class="us">GetCVmax</th><th>ChatGPT</th><th>Resume builders</th><th>CV writer</th></tr></thead>
  <tbody>
    <tr><td>Verdict on every line</td><td class="us">Yes</td><td class="no">If you ask</td><td class="no">No</td><td>Yes</td></tr>
    <tr><td>Never invents numbers or skills</td><td class="us">Yes, asks you</td><td class="no">Often invents</td><td class="no">Fills templates</td><td>Yes</td></tr>
    <tr><td>Rewritten for one job ad</td><td class="us">Yes</td><td>Yes</td><td class="no">Keywords only</td><td class="no">Extra fee</td></tr>
    <tr><td>Finds experience you forgot</td><td class="us">Question interview</td><td class="no">No</td><td class="no">No</td><td>In a call</td></tr>
    <tr><td>Ready PDF and DOCX</td><td class="us">Yes</td><td class="no">No</td><td>Yes</td><td>Yes</td></tr>
    <tr><td>Time</td><td class="us">~10 minutes</td><td class="no">An evening of prompts</td><td class="no">1–2 hours</td><td class="no">3–7 days</td></tr>
    <tr><td>Price</td><td class="us">Free while we launch</td><td class="no">Free or monthly plan</td><td class="no">Monthly fee</td><td class="no">Hundreds of dollars</td></tr>
  </tbody>
</table></div>
""",
    unsafe_allow_html=True,
)

# ---------- Founder ----------
st.markdown(
    """
<div class="cvx-section">
<div class="cvx-founder">
  <div class="mono">M</div>
  <div class="letter">
    <span class="cvx-label">who made this</span>
    <p><b>Hey, it's Marian.</b> I had a dream job, a dream field and a CV. What I didn't have was a plan to get from one to the other.</p>
    <p>So I sat down with an AI and asked two questions. Which internships can I get with this CV today? And what do I need to change, learn and add to get the job I actually want?</p>
    <p>We went through it line by line: what a recruiter in that field looks for, what to cut, what to learn next and how to put it on the page. No invented numbers, only things I had really done, written so they count.</p>
    <p>That plan became GetCVmax. Upload your CV, name your dream job, and get the same thing: every line fixed and a clear list of what to do next.</p>
    <span class="sig">Marian</span>
  </div>
</div>
</div>
""",
    unsafe_allow_html=True,
)

# ---------- Free launch ----------
st.markdown(
    f"""
<div class="cvx-section" id="free">
  <span class="cvx-label">free launch</span>
  <h2>Free while we launch</h2>
  <p>No payment, no card, nothing to cancel. {MAIN_LIMIT[0].upper() + MAIN_LIMIT[1:]}, and there are smaller
  daily limits on the other tools, so it stays free for everyone.</p>
</div>
<div class="cvx-plans one">
  <div class="cvx-plan hot"><span class="tag">Free</span>
    <h4>Everything included</h4>
    <ul><li>Score out of 100 on 7 criteria</li><li>A verdict on every line</li><li>Rewrites for your job ad</li>
      <li>The Q&amp;A interview that finds results you forgot</li><li>One-page PDF and DOCX download</li>
      <li>Vetted learning resources, each checked by a person</li><li>Live job links</li>
      <li>A shareable score card</li></ul>
  </div>
</div>
<p class="cvx-note">Paid options may come later. We will email you before anything changes, and everything you
made stays yours.</p>
""",
    unsafe_allow_html=True,
)

with st.form("waitlist", border=False):
    st.markdown("**Get one email when something new ships.** No spam.")
    w1, w3 = st.columns([3, 1], vertical_alignment="bottom")
    email = w1.text_input("Your email", placeholder="you@email.com", max_chars=254, key="waitlist_email")
    sent = w3.form_submit_button("Notify me", type="primary", width="stretch")
if sent:
    if not EMAIL.match(email.strip()):
        st.error("Check your email address: it looks incomplete.")
    elif join_waitlist(email, "full", st.query_params.get("ref")):
        st.success("You're on the list. We'll email you once when something new ships.")

# ---------- FAQ ----------
st.markdown('<div class="cvx-section"><span class="cvx-label">questions</span><h2>Before you start</h2></div>',
            unsafe_allow_html=True)
faq = [
    ("Is it free?", f"Yes, while we launch. {MAIN_LIMIT[0].upper() + MAIN_LIMIT[1:]}, "
     "and there are smaller daily limits on the other tools. The limits reset daily. There is no subscription. Paid options may come later; we will email you "
     "before anything changes, and everything you made stays yours."),
    ("Will it make things up?", "No. It only rewrites what's in your CV or what you tell it in the Q&A. "
     "Where a number is missing, it leaves a gap like [N] and asks you to fill it."),
    ("Do you store my CV?", "Not the file. It is sent to the model only for the review. "
     + ("Your results are saved in your account, and you can delete them at any time. " if ACCOUNTS else
        "Results stay only in your browser tab, so download your PDF before you close it. ")
     + "Details are on the Privacy page."),
    ("Which CVs does it work with?", "English CVs in PDF or DOCX, for internships and first jobs in business, "
     "economics and data, software, AI, psychology, law and more. Each field has its own checklist."),
    ("Will this get me a job?", "No tool can promise that. GetCVmax makes your CV stronger and shows what to "
     "learn next, but the employer makes the call. AI can make mistakes, so check every edit."),
]
for q, a in faq:
    with st.expander(q):
        st.write(a)

# ---------- Final call ----------
st.markdown('<div class="cvx-section"><h2>Next application, send the fixed one.</h2></div>', unsafe_allow_html=True)
if st.button("Fix my CV", type="primary", icon=":material/arrow_forward:", key="cta_bottom"):
    st.switch_page("views/analyze.py")

st.divider()
with st.container(horizontal=True, gap="medium"):
    st.page_link("views/about.py", label="About")
    st.page_link("views/privacy.py", label="Privacy")
    st.page_link("views/terms.py", label="Terms")
    st.page_link("views/feedback.py", label="Feedback")
st.markdown('<div class="cvx-footer">GetCVmax · made by a student, for students who apply abroad</div>',
            unsafe_allow_html=True)
