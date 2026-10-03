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
    return "demo mode" if is_demo() else get_llm().provider


def demo_banner() -> None:
    if is_demo():
        st.info("Demo mode: no model key is set, so the answers are pre-written. Use it to look around the interface.")


PRIVACY_NOTE = {
    "Google Gemini API": " On the free tier Google may review submitted data and use it to improve its products, "
    "so remove your phone number and address from the CV first.",
}


def consent(page: str) -> bool:
    """Згода на обробку. Дається один раз і діє на всіх сторінках до закриття вкладки."""
    provider = provider_name()
    already = st.session_state.get("consented", False)
    box = st.container() if already else card(f"consent-{page}")
    with box:
        if not already:
            st.markdown("**Before you start**")
            st.caption("Your CV contains personal data, so we need your consent. Once per session.")
        given = st.checkbox(
            f"I agree that my CV and answers are sent to {provider} for analysis only. "
            "GetCVmax does not store your CV file; it disappears when you close the tab."
            + PRIVACY_NOTE.get(provider, ""),
            value=already,
            key=f"consent_{page}",
        )
        st.page_link("views/privacy.py", label="How we handle your data", icon=":material/shield:")
    st.session_state["consented"] = given
    return given


def cv_picker(page: str) -> CVFile | None:
    """Завантаження CV. Раз завантажене CV доступне на всіх сторінках."""
    current: CVFile | None = st.session_state.get("cv_file")
    if current is not None:
        c1, c2 = st.columns([3, 1])
        c1.success(f"CV: {current.filename}")
        if c2.button("Another CV", key=f"replace_cv_{page}"):
            del st.session_state["cv_file"]
            st.rerun()
        return current

    uploaded = st.file_uploader("PDF or DOCX, in English", type=["pdf", "docx"], key=f"upload_{page}")
    if uploaded is None:
        if is_demo():
            st.caption("In demo mode you can skip the upload: a sample CV is used.")
            return CVFile(filename="demo_cv.txt", text=DEMO_CV_TEXT)
        return None
    try:
        data = uploaded.getvalue()
        if len(data) > config.MAX_FILE_MB * 1024 * 1024:
            raise CVReadError(f"The file is larger than {config.MAX_FILE_MB} MB.")
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
