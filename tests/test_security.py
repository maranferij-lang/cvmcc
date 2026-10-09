"""Protections from the security audit: escaping of model text, request limit, generic database errors."""

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
    assert "`https://evil.example/p?d=a@b.c)`" in out  # the address is visible, but as code, not a link
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


def test_lost_facts_flags_numbers_dropped_by_a_rewrite():
    from cvmax.edits import lost_facts

    before = "- Ran a loyalty campaign for 3 cafes that grew repeat customers from 18% to 26% in two months"
    assert lost_facts(before, "- Managed social media for [N] locations, growing engagement by [X]%") == ["3", "18%", "26%"]
    assert lost_facts(before, "- Grew repeat customers at 3 cafes from 18% to 26% in two months with a loyalty campaign") == []
    assert lost_facts(before, "") == []  # deletion is checked separately, with the verdict cut


def test_fact_checker_accepts_ukrainian_names():
    from cvmax.edits import unverified_terms
    known = "Відбір у ШІ-школу КШЕ, робив звіти в ексель"
    assert unverified_terms("Built a pipeline for the KSE AI School in Excel", known) == []
    assert unverified_terms("Built dashboards in Tableau", known) == ["Tableau"]

    assert unverified_terms("Used AI tools", "Інші проєкти, кращі результати") == ["AI"]
