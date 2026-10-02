"""Privacy policy."""

import streamlit as st

from ui.account import auth_configured
from ui.common import card, provider_name, secret

provider = provider_name()
contact = secret("CVMAX_CONTACT")
ACCOUNTS = auth_configured()  # without login nothing is tied to a person

st.title("Privacy")
st.caption("Last updated: October 2, 2026")
st.markdown(
    '<p class="cvx-page-lead">A CV contains personal data, so we collect the minimum and store even less. '
    + (
        "In short: your CV file is never stored, results live only in your account, and you can delete "
        "everything with one click.</p>"
        if ACCOUNTS
        else "In short: no sign-up, your CV file is never stored, and results disappear when you close the tab.</p>"
    ),
    unsafe_allow_html=True,
)

with card("privacy-short"):
    c1, c2, c3 = st.columns(3)
    c1.markdown("**CV file**  \nNever stored. Lives only in your browser tab.")
    c2.markdown(
        "**Results**  \n"
        + ("In your account until you delete them." if ACCOUNTS else "Only in the tab. Download your PDF before you close it.")
    )
    c3.markdown("**Ads and data sales**  \nNever. We don't sell your data or share it with advertisers.")

st.header("Who processes your data")
st.write(
    "CVmax is an independent project. The author is responsible for processing your data."
    + (f" Contact: {contact}." if contact else " You can reach us through the Feedback page.")
)

st.header("What data we receive")
st.markdown(
    "- **Your CV file** and the text extracted from it.\n"
    "- **What you type in forms:** your goal, company type, job posting, answers in the Q&A and the CV builder.\n"
    "- **Your Google account, if you sign in:** email and name. We never see your password.\n"
    "- **Profile and saved results, if you sign in:** field of study, year, goal, reviews, roles, built CVs. "
    "Results may include quotes from your CV.\n"
    "- **Which edits you accepted:** the before/after text, the section, the target role and whether it was "
    "accepted. We record this when you download your CV.\n"
    "- **Daily usage counters**, so the limits work.\n"
    "- **Feedback:** the rating and text you choose to send. Stored anonymously, without your email."
)
st.write("We don't collect visit analytics, use ad trackers or buy data about you.")

st.header("Why")
st.markdown(
    "- To review your CV and show you the results.\n"
    + ("- To save your results so you can come back to them.\n" if ACCOUNTS else "")
    + "- To enforce daily limits so the service stays free.\n"
    "- To see which advice is useful and improve it. For this we only need the text of edits, not CV files."
)
st.write(
    "Legal basis: your consent, which you give before uploading your CV. "
    + ("You can withdraw it at any time by deleting your data in your account." if ACCOUNTS
       else "Withdrawing it is simple: close the tab, and your CV file and results are gone.")
)

st.header("Who we share data with")
st.markdown(
    f"- **{provider}:** your CV and answers are sent to the model for review.\n"
    "- **Streamlit Community Cloud (Snowflake, USA):** hosts the site. All requests go through it.\n"
    "- **Supabase (EU servers, Frankfurt):** a database with usage counters, accepted edits and feedback"
    + (", plus your profile and results.\n- **Google:** sign-in." if ACCOUNTS else ".")
)
if provider == "Google Gemini API":
    st.warning(
        "CVmax currently uses the free tier of Gemini. On this tier, Google may review the data you send "
        "and use it to improve its products. So before uploading, remove your phone number, address and "
        "any other details the review doesn't need."
    )

st.header("How long we keep it")
st.markdown(
    "- **CV file:** until you close the browser tab.\n"
    + (
        "- **Profile, results, accepted edits, counters:** until you delete them.\n"
        if ACCOUNTS
        else "- **Results:** also until you close the tab.\n"
        "- **Accepted edits and counters:** kept, but not linked to you (see Your rights).\n"
    )
    + "- **At the model and hosting providers:** under their own rules. On the free Gemini tier, Google may "
    "keep requests longer than we do."
)

st.header("How we protect it")
st.markdown(
    "- The site runs over HTTPS only.\n"
    "- Model and database keys are kept in server secrets, not in the code or the browser.\n"
    "- Database tables are closed to direct access. The site reaches them only through a few functions, each "
    "of which checks a secret server token.\n"
    + ("- You only see your own results: every database request is tied to the email from your Google sign-in.\n"
       if ACCOUNTS else "")
    + "- CV files are parsed in a separate process with memory and time limits, so a malicious file can't break "
    "the site.\n"
    "- We don't write CV contents to server logs."
)

st.header("Your rights")
if ACCOUNTS:
    rights = (
        "- **Delete everything:** My account → Data and sign out. This deletes your profile, results, accepted "
        "edits and counters.\n"
        "- **Change your profile:** My account → Profile.\n"
        "- **See what we store about you:** all your results are visible in your account"
        + (f", and for anything else write to {contact}.\n" if contact else ".\n")
    )
else:
    rights = (
        "- **Without sign-in, we don't know who you are.** Counters and accepted edits are tied to a random "
        "tab ID, so they can't be linked to you.\n"
        "- **Delete your results:** close the tab.\n"
    )
st.markdown(rights + "- **Complain:** to your local data protection authority (in the EU, your national DPA).")

st.header("Cookies")
st.write(
    "The site uses only technical cookies: the Streamlit session"
    + (" and the Google sign-in cookie" if ACCOUNTS else "")
    + ". The site doesn't work without them. There are no analytics or advertising cookies."
)

st.header("Other")
st.markdown(
    "- Don't upload someone else's CV without their consent.\n"
    "- The service is meant for people aged 16 and over.\n"
    "- If we change these rules, the new version will appear on this page with a new date."
)
