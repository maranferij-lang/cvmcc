"""Спільне для всіх сторінок: модель, згода на обробку, завантаження CV, стилі."""

from __future__ import annotations

import os

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
    given = st.checkbox(
        f"Я погоджуюсь на обробку мого CV. CV містить персональні дані. Він надсилається в {provider} "
        "тільки для аналізу, CVMAX його ніде не зберігає. Після закриття вкладки дані зникають."
        + PRIVACY_NOTE.get(provider, ""),
        value=st.session_state.get("consented", False),
        key=f"consent_{page}",
    )
    st.session_state["consented"] = given
    if given:
        st.page_link("views/privacy.py", label="Як ми обробляємо дані", icon=":material/shield:")
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


CSS = """
<style>
.block-container {padding-top: 3.5rem;}
.cvx-hero {padding: 2.2rem 0 1.2rem 0;}
.cvx-hero h1 {font-size: 3rem; line-height: 1.1; margin: 0 0 .8rem 0; padding: 0;}
.cvx-hero p {font-size: 1.15rem; opacity: .8; max-width: 38rem; margin: 0;}
.cvx-badge {display: inline-block; font-size: .8rem; font-weight: 600; letter-spacing: .02em;
  padding: .25rem .7rem; border-radius: 999px; margin-bottom: 1rem;
  background: rgba(91, 79, 233, .12); color: #5B4FE9;}
.cvx-step {font-size: 2rem; font-weight: 700; color: #5B4FE9; line-height: 1;}
.cvx-footer {opacity: .6; font-size: .85rem; padding-top: 1rem;}
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
