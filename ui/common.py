"""Спільне для всіх сторінок: модель, згода на обробку, завантаження CV, стилі."""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from cvmax import config
from cvmax.cv_input import CVFile, CVReadError, load_cv
from cvmax.demo import DEMO_CV_TEXT, FakeClient
from cvmax.llm import LLMError, make_llm


def secret(name: str) -> str | None:
    try:
        value = st.secrets.get(name)
    except Exception:  # немає secrets.toml
        value = None
    return value or os.environ.get(name)


@st.cache_resource
def _llm():
    llm = make_llm(gemini_key=secret("GEMINI_API_KEY"), anthropic_key=secret("ANTHROPIC_API_KEY"))
    return llm if llm is not None and os.environ.get("CVMAX_DEMO") != "1" else FakeClient()


def get_llm():
    try:
        return _llm()
    except LLMError as e:
        st.error(str(e))
        st.stop()


def is_demo() -> bool:
    return isinstance(get_llm(), FakeClient)


def provider_name() -> str:
    return "демо" if is_demo() else get_llm().provider


def demo_banner() -> None:
    if is_demo():
        st.info("Демо-режим: ключ моделі не налаштовано, тому відповіді заготовлені. Так можна подивитись інтерфейс.")


PRIVACY_NOTE = {
    "Google Gemini API": " На безплатному тарифі Google може переглядати надіслані дані і використовувати їх "
    "для покращення своїх продуктів, тому краще прибери з CV телефон і адресу.",
}


def consent(page: str) -> bool:
    """Згода на обробку. Дається один раз і діє на всіх сторінках до закриття вкладки."""
    provider = provider_name()
    already = st.session_state.get("consented", False)
    box = st.container() if already else card(f"consent-{page}")
    with box:
        if not already:
            st.markdown("**Перед початком**")
            st.caption("CV містить персональні дані, тому нам потрібна твоя згода. Це один раз за сесію.")
        given = st.checkbox(
            f"Я погоджуюсь, що моє CV і відповіді надсилаються в {provider} лише для аналізу. "
            "CVmax не зберігає файл CV, він зникає після закриття вкладки."
            + PRIVACY_NOTE.get(provider, ""),
            value=already,
            key=f"consent_{page}",
        )
        st.page_link("views/privacy.py", label="Як ми обробляємо дані", icon=":material/shield:")
    st.session_state["consented"] = given
    return given


def cv_picker(page: str) -> CVFile | None:
    """Завантаження CV. Раз завантажене CV доступне на всіх сторінках."""
    current: CVFile | None = st.session_state.get("cv_file")
    if current is not None:
        c1, c2 = st.columns([3, 1])
        c1.success(f"CV: {current.filename}")
        if c2.button("Інше CV", key=f"replace_cv_{page}"):
            del st.session_state["cv_file"]
            st.rerun()
        return current

    uploaded = st.file_uploader("PDF або DOCX, англійською", type=["pdf", "docx"], key=f"upload_{page}")
    if uploaded is None:
        if is_demo():
            st.caption("У демо-режимі можна не завантажувати CV: буде використано приклад.")
            return CVFile(filename="demo_cv.txt", text=DEMO_CV_TEXT)
        return None
    try:
        data = uploaded.getvalue()
        if len(data) > config.MAX_FILE_MB * 1024 * 1024:
            raise CVReadError(f"Файл більший за {config.MAX_FILE_MB} МБ.")
        cv = load_cv(uploaded.name, data)
    except CVReadError as e:
        st.error(str(e))
        return None
    st.session_state["cv_file"] = cv
    return cv


STYLE_FILE = Path(__file__).resolve().parents[1] / "assets" / "style.css"
BOT_AVATAR = str(Path(__file__).resolve().parents[1] / "assets" / "icon.png")


def _css() -> str:
    return STYLE_FILE.read_text(encoding="utf-8")


def inject_css() -> None:
    """Стилі сайту. Викликається один раз на кожен показ сторінки (в app.py)."""
    st.html(f"<style>{_css()}</style>")


def card(key: str, **kwargs):
    """Скляна картка. Ключ потрібен, щоб CSS її знайшов (клас st-key-card-...)."""
    return st.container(key=f"card-{key}", **kwargs)
