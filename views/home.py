"""Home: what CVmax is and where to start."""

import base64

import streamlit as st

from cvmax import config
from cvmax.cv_render import pdf_preview, render_pdf
from cvmax.demo import demo_built_cv
from ui.account import auth_configured

st.html('<div class="cvx-wide"></div>')  # wider page, see assets/style.css
ACCOUNTS = auth_configured()  # without login, results live only in the tab


@st.cache_data
def cv_thumbnail() -> str:
    """First page of the demo CV in our template, to show the PDF the user gets."""
    png = pdf_preview(render_pdf(demo_built_cv()), scale=1.2)[0]
    return base64.b64encode(png).decode()


@st.cache_data
def asset(name: str) -> str:
    """3D renders from assets/ as base64, so they sit inside the page HTML."""
    return base64.b64encode(open(f"assets/{name}", "rb").read()).decode()


# ---------- Hero ----------
left, right = st.columns([1.05, 1], gap="large", vertical_alignment="center")
with left:
    st.markdown(
        """
<div class="cvx-hero">
  <span class="cvx-eyebrow">Free AI CV coach</span>
  <h1>Get your CV <em class="hl">noticed</em></h1>
  <p class="lead">Upload your CV and a job posting. CVmax shows what a recruiter will skip and what will land,
  rewrites weak lines and asks about experience you undersell. Never makes things up.</p>
</div>
""",
        unsafe_allow_html=True,
    )
    c1, c2 = st.columns(2)
    if c1.button("Review my CV", type="primary", icon=":material/arrow_forward:", width="stretch"):
        st.switch_page("views/analyze.py")
    if c2.button("No CV? Build one", width="stretch"):
        st.switch_page("views/builder.py")
    st.caption(("Sign in with Google" if ACCOUNTS else "No sign-up") + " · PDF or DOCX in English · your file is never stored")

with right:
    st.markdown(
        f"""
<div class="cvx-stage">
  <img src="data:image/webp;base64,{asset('hero.webp')}" alt="A CV under a glass card showing a fit score of 84">
  <div class="cvx-glass cvx-glass-edit">
    <span class="tag">After</span>Built <span class="cvx-ph">[N]</span> weekly Excel sales reports for the
    regional team, cutting prep time by <span class="cvx-ph">[X]%</span>
  </div>
  <div class="cvx-glass cvx-glass-q">How many people came to the events you organized?</div>
</div>
""",
        unsafe_allow_html=True,
    )

# ---------- Features ----------
st.markdown(
    f"""
<div class="cvx-section">
  <div class="kicker">What you get</div>
  <h2>Not generic tips. Edits for your goal.</h2>
  <p>The same line can be strong for consulting and useless for software. CVmax judges every line of your CV
  against the role you chose and tells you why.</p>
</div>
<div class="cvx-bento">
  <div class="cvx-tile w4">
    <img class="cvx-ico" alt="" src="data:image/webp;base64,{asset('ico_check.webp')}">
    <h4>Every line checked</h4>
    <p>Keep, shorten, rewrite or cut. CVmax flags bullets with no action or result, duplicates and bragging
    that doesn't serve your goal. If your CV runs past one page, it tells you what to cut first.</p>
    <div class="cvx-diff">
      <div class="was"><span class="tag">Cut</span>Interests: travelling, music, gym</div>
      <div class="now"><span class="tag">Keep</span>Chess: 2nd place, national U-18 championship</div>
    </div>
  </div>
  <div class="cvx-tile">
    <img class="cvx-ico" alt="" src="data:image/webp;base64,{asset('ico_chat.webp')}">
    <h4>{config.GRILL_MAX_QUESTIONS} questions in the Q&amp;A</h4>
    <p>Asks about numbers, scale and experience missing from your CV. Your answers become new bullets.</p>
    <div class="cvx-bubbles">
      <div class="cvx-bubble">Have you built anything with AI for yourself?</div>
      <div class="cvx-bubble me">A script that pulls competitor prices into a sheet every day</div>
    </div>
  </div>
  <div class="cvx-tile">
    <img class="cvx-ico" alt="" src="data:image/webp;base64,{asset('ico_arrow.webp')}">
    <h4>Where to apply</h4>
    <p>Roles where your CV has the best odds, and what to search for on LinkedIn and job boards.</p>
    <div class="cvx-fit">
      <div><div class="cvx-row"><span>Data Analyst</span><span>78%</span></div><div class="cvx-bar"><i style="width:78%"></i></div></div>
      <div><div class="cvx-row"><span>BA, consulting</span><span>64%</span></div><div class="cvx-bar"><i style="width:64%"></i></div></div>
    </div>
  </div>
  <div class="cvx-tile">
    <h4>Skills to build</h4>
    <p>The gaps that will raise your odds the most, with time estimates.</p>
    <ul class="cvx-list">
      <li>SQL: joins, window functions <span>3–4 weeks</span></li>
      <li>IELTS Academic 7.0+ <span>1–2 months</span></li>
    </ul>
  </div>
  <div class="cvx-tile">
    <h4>Final CV as a PDF</h4>
    <p>Your accepted edits go straight into a clean template that recruiters and ATS can both read. Or get a DOCX to keep editing.</p>
    <div class="cvx-paper"><img alt="Sample CV in the CVmax template" src="data:image/png;base64,{cv_thumbnail()}"></div>
  </div>
</div>
""",
    unsafe_allow_html=True,
)

# ---------- How it works ----------
st.markdown(
    """
<div class="cvx-section">
  <div class="kicker">How it works</div>
  <h2>Three steps, about ten minutes</h2>
</div>
<div class="cvx-steps">
  <div class="cvx-step"><b class="n">1</b><div><div class="t">Tell us your goal</div>
    <div class="d">The role, the type of company and, ideally, the full job posting. Consulting and fintech look for different things.</div></div></div>
  <div class="cvx-step"><b class="n">2</b><div><div class="t">Upload your CV</div>
    <div class="d">PDF or DOCX in English. The file lives only in your browser tab and is never stored.</div></div></div>
  <div class="cvx-step"><b class="n">3</b><div><div class="t">Accept edits and download your PDF</div>
    <div class="d">You approve every edit yourself. The Q&amp;A adds what you forgot to mention.</div></div></div>
</div>
""",
    unsafe_allow_html=True,
)

# ---------- Principles ----------
st.markdown(
    f"""
<div class="cvx-section">
  <div class="kicker">Principles</div>
  <h2>Honest, tailored, minimal data</h2>
</div>
<div class="cvx-principles">
  <div><b>Never makes things up</b><span>Where a number is missing, it puts [X] and asks. Facts that aren't in your CV
  or your answers get flagged as suspicious.</span></div>
  <div><b>Built for your field</b><span>Criteria for 6 fields: economics &amp; data, business, software, AI, psychology
  and law, built from 47 sources on how recruiters read CVs.</span></div>
  <div><b>Minimal data</b><span>Your CV file is never stored. {"Results live only in your account, and you can delete them with one click." if ACCOUNTS else "No sign-up needed, and results live only in your browser tab."}</span></div>
</div>
""",
    unsafe_allow_html=True,
)

# ---------- FAQ ----------
st.markdown('<div class="cvx-section"><div class="kicker">FAQ</div><h2>Frequently asked questions</h2></div>',
            unsafe_allow_html=True)
faq = [
    ("Is it free?", "Yes. CVmax is free to use. To keep it that way, there are daily limits that reset every day."),
    ("Do you store my CV?", "Not the file. It is sent to the model only for the review and disappears when you close "
     "the tab. "
     + ("Your review results are saved in your account so you can come back to them, "
        "and you can delete them at any time. " if ACCOUNTS else
        "There is no sign-up, so results also stay only in the tab: download your PDF before you close it. ")
     + "Details are on the Privacy page."),
    ("How is this better than ChatGPT?", "CVmax knows the criteria for your field and goal, checks every line of your "
     "CV, asks instead of making things up, and gives you edits you can accept one by one. At the end it builds "
     "a ready-to-send PDF."),
    ("Does my CV have to be in English?", "Yes, CVmax targets international companies. You can get advice "
     "in English or Ukrainian."),
    ("Will this get me a job?", "No tool can promise that. CVmax helps you make your CV stronger and pick realistic "
     "roles, but the employer makes the call. The model can make mistakes, so check every edit."),
]
for q, a in faq:
    with st.expander(q):
        st.write(a)

# ---------- Call to action ----------
with st.container(key="cta", horizontal_alignment="center"):
    st.markdown(
        '<div class="cvx-cta-text"><h2>Check your CV against your dream job</h2>'
        "<p>Your first review takes about a minute.</p></div>",
        unsafe_allow_html=True,
    )
    with st.container(horizontal=True, horizontal_alignment="center"):
        if st.button("Start review", type="primary", icon=":material/arrow_forward:", key="cta_bottom"):
            st.switch_page("views/analyze.py")
        if st.button("Where should I apply?", key="cta_career"):
            st.switch_page("views/career.py")

st.divider()
with st.container(horizontal=True, gap="medium"):
    st.page_link("views/about.py", label="About")
    st.page_link("views/privacy.py", label="Privacy")
    st.page_link("views/terms.py", label="Terms")
    st.page_link("views/feedback.py", label="Feedback")
st.markdown(
    '<div class="cvx-footer">© 2026 CVmax · independent project</div>',
    unsafe_allow_html=True,
)
