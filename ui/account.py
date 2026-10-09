"""Account: Google sign-in, profile, daily limits, saved results.

Sign-in is turned on when Streamlit Secrets has an [auth] section. Without it the site works anonymously:
limits are counted per browser tab, and results are not saved.
"""

from __future__ import annotations

import logging
import uuid

import streamlit as st

from cvmax import config
from cvmax.learning.events import make_payload
from cvmax.db import DBError, MemoryDB, SupabaseDB
from ui.common import card, secret

log = logging.getLogger("cvmax")

LIMIT_NAMES = {
    "analysis": "CV reviews",
    "career": "career searches",
    "grill": "Q&A sessions",
    "builder": "builder interviews",
    "build": "CV builds",
    "export": "PDF exports",
    "feedback": "feedback messages",
    "jobs": "job searches",
}


def auth_configured() -> bool:
    """Google sign-in is fully configured. If placeholders remain in Secrets, the site works without sign-in."""
    try:
        auth = st.secrets.get("auth")
        client_id = str(auth["google"]["client_id"]) if auth else ""
    except Exception:
        return False
    return client_id.endswith(".apps.googleusercontent.com") and "ВСТАВ" not in client_id


@st.cache_resource
def _fallback_db() -> MemoryDB:
    """In-memory counters for when Supabase is unavailable: the limits do not switch off."""
    return MemoryDB()


@st.cache_resource
def get_db():
    url, key, token = secret("SUPABASE_URL"), secret("SUPABASE_KEY"), secret("CVMAX_DB_TOKEN")
    if url and key and token:
        return SupabaseDB(url, key, token)
    return MemoryDB()


def is_logged_in() -> bool:
    """Signed in through Google and has a verified email: without verification we cannot be sure the email is theirs."""
    if not auth_configured() or not getattr(st.user, "is_logged_in", False):
        return False
    return st.user.get("email_verified") in (True, "true")


def current_email() -> str | None:
    return str(st.user.email).lower() if is_logged_in() and st.user.get("email") else None


def user_row() -> dict | None:
    """The user's record in the database. Created at the first sign-in, cached for the session."""
    email = current_email()
    if email is None:
        return None
    cached = st.session_state.get("user_row")
    if cached and cached.get("email") == email:
        return cached
    try:
        row = get_db().touch_user(email, st.user.get("name"))
    except DBError as e:
        log.warning("touch_user failed: %s", e)
        row = {"email": email, "name": st.user.get("name"), "onboarded_at": "unknown"}
    st.session_state["user_row"] = row
    return row


def needs_onboarding() -> bool:
    row = user_row()
    return row is not None and not row.get("onboarded_at")


def save_profile(program: str, status: str, background: str, goal: str) -> None:
    email = current_email()
    if email is None:
        return
    get_db().save_profile(email, program, status, background, goal)
    row = dict(st.session_state.get("user_row") or {})
    row.update(program=program, status=status, background=background, goal=goal, onboarded_at="now")
    st.session_state["user_row"] = row


def profile_value(field: str, default=None):
    row = user_row() or {}
    return row.get(field) or default


def require_login(page_title: str) -> None:
    """On the tool pages: if sign-in is on and the user is not signed in, show the sign-in and stop."""
    if not auth_configured() or is_logged_in():
        return
    st.title(page_title)
    if getattr(st.user, "is_logged_in", False):  # signed in, but Google did not verify the email
        st.warning("Google has not verified this account's email. Sign in with another account.")
        st.button("Sign out", on_click=st.logout)
        st.stop()
    with card("login"):
        st.subheader("Sign in to continue")
        st.write(
            "Signing in with Google takes a few seconds. We will save your results "
            "so you can come back to them later. GetCVmax is free."
        )
        st.button("Sign in with Google", type="primary", icon=":material/login:", on_click=st.login, args=("google",))
    st.stop()


def _user_key() -> str:
    email = current_email()
    if email:
        return email
    if "anon_id" not in st.session_state:
        st.session_state["anon_id"] = "anon:" + uuid.uuid4().hex
    return st.session_state["anon_id"]


def take_limit(kind: str) -> bool:
    """Spends one use. If the limit is used up, shows an explanation and returns False."""
    user_limit, global_limit = config.LIMITS[kind]
    try:
        result = get_db().consume(_user_key(), kind, user_limit, global_limit)
    except DBError as e:
        log.warning("consume failed, counting in memory: %s", e)
        result = _fallback_db().consume(_user_key(), kind, user_limit, global_limit)
    if result.get("allowed"):
        return True
    what = LIMIT_NAMES.get(kind, "requests")
    if result.get("reason") == "user":
        st.warning(f"You have used all {result.get('limit')} {what} for today. Come back tomorrow.")
    else:
        st.warning(
            "GetCVmax is free, and today's site-wide limit has been reached. Come back tomorrow; limits reset daily."
        )
    return False


def save_result(kind: str, title: str, payload: dict) -> None:
    email = current_email()
    if email is None or not get_db().persistent:
        return
    try:
        get_db().save_result(email, kind, title, payload)
        st.toast("Saved to My account", icon=":material/bookmark:")
    except DBError as e:
        log.warning("save_result failed: %s", e)
        st.toast("Could not save the result, but it is still on this page.")


def log_edit_feedback(analysis_id: str, target_role: str, program: str, items: list[dict]) -> None:
    """Saves which edits were accepted. An error here must not stop the user from downloading the CV."""
    if not items or not get_db().persistent:
        return
    try:
        get_db().log_edit_feedback(_user_key(), analysis_id, target_role, program, items)
    except DBError as e:
        log.warning("log_edit_feedback failed: %s", e)


def send_feedback(page: str, rating: int | None, message: str) -> bool:
    """Saves anonymous feedback. Returns True if it succeeded."""
    if not take_limit("feedback"):
        return False
    try:
        get_db().save_feedback(page, rating, message.strip()[:2000])
    except DBError as e:
        log.warning("save_feedback failed: %s", e)
        st.error("Could not send your feedback. Try again a bit later.")
        return False
    return True


def join_waitlist(email: str, plan: str, source: str | None = None) -> bool:
    """Writes the email to the waitlist. Returns True if it succeeded."""
    if not take_limit("feedback"):
        return False
    try:
        get_db().join_waitlist(email.strip().lower()[:254], plan, source)
    except DBError as e:
        log.warning("join_waitlist failed: %s", e)
        st.error("Couldn't save your email. Try again in a minute.")
        return False
    return True


def limit_caption(kind: str) -> None:
    """Shows the daily limit in advance, so it is not a surprise in the middle of work."""
    user_limit, _ = config.LIMITS[kind]
    st.caption(f"Free: up to {user_limit} {LIMIT_NAMES.get(kind, 'requests')} a day. Limits reset daily.")



def log_event(kind: str, /, **fields) -> None:
    """Writes an event for self-learning. Never raises an exception and shows nothing to the user."""
    try:
        payload = make_payload(kind, **fields)
        get_db().log_event(_user_key(), kind, payload)
    except Exception as e:  # learning must not break the page
        log.warning("log_event %s failed: %s", kind, e)


@st.cache_data(ttl=600, show_spinner=False)
def variant_stats_cached(task: str) -> list[dict]:
    """Prompt variant statistics, cached for 10 minutes."""
    try:
        return get_db().variant_stats(task)
    except DBError as e:
        log.warning("variant_stats failed: %s", e)
        return []


@st.cache_data(ttl=600, show_spinner=False)
def skill_demand_cached(program: str, region: str) -> list[dict]:
    """Skills requested in job postings, cached for 10 minutes."""
    try:
        return get_db().skill_demand(program, region)
    except DBError as e:
        log.warning("skill_demand failed: %s", e)
        return []
