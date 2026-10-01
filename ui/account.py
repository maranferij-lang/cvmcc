"""Акаунт: вхід через Google, профіль, денні ліміти, збережені результати.

Вхід вмикається, коли в Streamlit Secrets є розділ [auth]. Без нього сайт працює анонімно:
ліміти рахуються на вкладку браузера, а результати не зберігаються.
"""

from __future__ import annotations

import logging
import uuid

import streamlit as st

from cvmax import config
from cvmax.db import DBError, MemoryDB, SupabaseDB
from ui.common import card, secret

log = logging.getLogger("cvmax")

LIMIT_NAMES = {
    "analysis": "аналізів CV",
    "career": "пошуків напрямів",
    "grill": "сесій Grill me",
    "builder": "інтерв'ю в конструкторі",
    "build": "збирань CV",
    "export": "оформлень CV",
}


def auth_configured() -> bool:
    try:
        return "auth" in st.secrets
    except Exception:
        return False


@st.cache_resource
def _fallback_db() -> MemoryDB:
    """Лічильники в пам'яті на випадок, коли Supabase недоступний: ліміти не вимикаються."""
    return MemoryDB()


@st.cache_resource
def get_db():
    url, key, token = secret("SUPABASE_URL"), secret("SUPABASE_KEY"), secret("CVMAX_DB_TOKEN")
    if url and key and token:
        return SupabaseDB(url, key, token)
    return MemoryDB()


def is_logged_in() -> bool:
    """Увійшов через Google і має підтверджений email: без підтвердження не можна бути певним, що email його."""
    if not auth_configured() or not getattr(st.user, "is_logged_in", False):
        return False
    return st.user.get("email_verified") in (True, "true")


def current_email() -> str | None:
    return str(st.user.email).lower() if is_logged_in() and st.user.get("email") else None


def user_row() -> dict | None:
    """Запис юзера в базі. Створюється при першому вході, кешується на сесію."""
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
    """На сторінках інструментів: якщо вхід увімкнено і юзер не увійшов, показати вхід і зупинитись."""
    if not auth_configured() or is_logged_in():
        return
    st.title(page_title)
    if getattr(st.user, "is_logged_in", False):  # увійшов, але Google не підтвердив email
        st.warning("Google не підтвердив email цього акаунта. Увійди з іншим акаунтом.")
        st.button("Вийти", on_click=st.logout)
        st.stop()
    with card("login"):
        st.subheader("Увійди, щоб продовжити")
        st.write(
            "Вхід через Google займає кілька секунд. Так ми збережемо твої результати, "
            "а ти зможеш повернутись до них пізніше. CVmax безплатний."
        )
        st.button("Увійти через Google", type="primary", icon=":material/login:", on_click=st.login, args=("google",))
    st.stop()


def _user_key() -> str:
    email = current_email()
    if email:
        return email
    if "anon_id" not in st.session_state:
        st.session_state["anon_id"] = "anon:" + uuid.uuid4().hex
    return st.session_state["anon_id"]


def take_limit(kind: str) -> bool:
    """Списує одне використання. Якщо ліміт вичерпано, показує пояснення і повертає False."""
    user_limit, global_limit = config.LIMITS[kind]
    try:
        result = get_db().consume(_user_key(), kind, user_limit, global_limit)
    except DBError as e:
        log.warning("consume failed, counting in memory: %s", e)
        result = _fallback_db().consume(_user_key(), kind, user_limit, global_limit)
    if result.get("allowed"):
        return True
    what = LIMIT_NAMES.get(kind, "запитів")
    if result.get("reason") == "user":
        st.warning(f"На сьогодні ти використав(-ла) всі {result.get('limit')} {what}. Повертайся завтра.")
    else:
        st.warning(
            "CVmax зараз безплатний, і сьогодні загальний ліміт вичерпано. Повертайся завтра, ліміт оновлюється щодня."
        )
    return False


def save_result(kind: str, title: str, payload: dict) -> None:
    email = current_email()
    if email is None or not get_db().persistent:
        return
    try:
        get_db().save_result(email, kind, title, payload)
        st.toast("Збережено в «Мій кабінет»", icon=":material/bookmark:")
    except DBError as e:
        log.warning("save_result failed: %s", e)
        st.toast("Не вдалося зберегти результат, але він є на цій сторінці.")


def log_edit_feedback(analysis_id: str, target_role: str, program: str, items: list[dict]) -> None:
    """Зберігає, які правки прийнято. Помилка тут не повинна заважати юзеру завантажити CV."""
    if not items or not get_db().persistent:
        return
    try:
        get_db().log_edit_feedback(_user_key(), analysis_id, target_role, program, items)
    except DBError as e:
        log.warning("log_edit_feedback failed: %s", e)
