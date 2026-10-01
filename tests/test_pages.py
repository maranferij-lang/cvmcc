"""Кожна сторінка відкривається в демо-режимі без помилок."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")
PAGES = ["views/home.py", "views/analyze.py", "views/career.py", "views/builder.py",
         "views/about.py", "views/privacy.py", "views/terms.py", "views/feedback.py"]


@pytest.fixture(autouse=True)
def demo_mode(monkeypatch):
    monkeypatch.setenv("CVMAX_DEMO", "1")
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(page):
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.switch_page(page).run()
    assert not at.exception


def test_analyze_flow_exports_pdf():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.switch_page("views/analyze.py").run()
    at.checkbox(key="consent_analyze").check().run()
    at.text_input(key="target_role").input("Business Analyst").run()
    next(b for b in at.button if b.label == "Проаналізувати CV").click().run()
    for box in at.checkbox:
        if str(box.key).startswith("accept_"):
            box.check()
    at.run()
    next(b for b in at.button if b.label.startswith("Оформити CV")).click().run()
    assert not at.exception
    labels = [el.proto.label for el in at.get("download_button")]
    assert "Завантажити PDF" in labels and "Завантажити DOCX" in labels


def test_feedback_page_sends_anonymous_feedback():
    from ui.account import get_db

    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.switch_page("views/feedback.py").run()
    at.text_area[0].input("Порадив прибрати важливий пункт").run()
    next(b for b in at.button if b.label == "Надіслати").click().run()
    assert not at.exception
    assert any("Дякуємо" in s.value for s in at.success)
