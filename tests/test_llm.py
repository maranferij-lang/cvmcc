"""ClaudeLLM: model response errors become LLMError rather than escaping as pydantic exceptions."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from cvmax.llm import ClaudeLLM, LLMError, ask_structured
from cvmax.schemas import GrillTurn

SECRET = "SECRET-CV-FRAGMENT"


def _validation_error() -> ValidationError:
    """A real schema error whose text contains the input data (like a model response with a CV fragment)."""
    try:
        GrillTurn.model_validate({"done": "not-a-bool", "kind": SECRET, "question": 1, "why_asking": None})
    except ValidationError as exc:
        assert SECRET in str(exc)  # without this the test would prove nothing
        return exc
    raise AssertionError("ValidationError expected")


def _client(parse):
    return SimpleNamespace(messages=SimpleNamespace(parse=parse))


def _ask(client):
    return ClaudeLLM(client=client).ask(
        system="s", content=[{"type": "text", "text": "x"}], output_model=GrillTurn, effort="low"
    )


def test_validation_error_from_parse_becomes_llm_error():
    exc = _validation_error()

    def parse(**kwargs):
        raise exc

    with pytest.raises(LLMError) as caught:
        _ask(_client(parse))
    assert SECRET not in str(caught.value)  # the model response text does not go to the user
    assert caught.value.__cause__ is exc


def test_validation_error_is_an_llm_error_for_ask_structured_too():
    def parse(**kwargs):
        raise _validation_error()

    with pytest.raises(LLMError):
        ask_structured(_client(parse), system="s", content=[{"type": "text", "text": "x"}],
                       output_model=GrillTurn, effort="low")


@pytest.mark.parametrize("reason, phrase", [("refusal", "declined"), ("max_tokens", "cut off")])
def test_stop_reason_checks_still_work(reason, phrase):
    ok = GrillTurn(done=False, kind="deepen", question="Q?", why_asking="why")
    client = _client(lambda **kw: SimpleNamespace(stop_reason=reason, parsed_output=ok))
    with pytest.raises(LLMError, match=phrase):
        _ask(client)


def test_missing_parsed_output_is_an_llm_error():
    client = _client(lambda **kw: SimpleNamespace(stop_reason="end_turn", parsed_output=None))
    with pytest.raises(LLMError, match="unexpected format"):
        _ask(client)


def test_valid_answer_is_returned():
    ok = GrillTurn(done=False, kind="deepen", question="Q?", why_asking="why")
    client = _client(lambda **kw: SimpleNamespace(stop_reason="end_turn", parsed_output=ok))
    assert _ask(client) is ok


# ---------- model choice by task weight ----------

def _capture():
    seen = {}

    def parse(**kwargs):
        seen.update(kwargs)
        ok = GrillTurn(done=False, kind="deepen", question="Q?", why_asking="why")
        return SimpleNamespace(stop_reason="end_turn", parsed_output=ok)

    return seen, _client(parse)


@pytest.mark.parametrize("effort, expected", [("low", "claude-haiku-5-5"), ("medium", "claude-sonnet-5-5"),
                                              ("high", "claude-sonnet-5-5")])
def test_light_tasks_use_haiku_and_heavy_tasks_use_sonnet(effort, expected):
    seen, client = _capture()
    ClaudeLLM(client=client).ask(system="s", content=[{"type": "text", "text": "x"}],
                                 output_model=GrillTurn, effort=effort)
    assert seen["model"] == expected
    assert "opus" not in seen["model"]  # the expensive model is never the default


def test_server_side_fallback_is_sent_only_to_models_that_support_it():
    seen, client = _capture()
    ClaudeLLM(client=client).ask(system="s", content=[{"type": "text", "text": "x"}],
                                 output_model=GrillTurn, effort="low")  # Haiku
    assert "extra_body" not in seen and "extra_headers" not in seen
    seen.clear()
    ClaudeLLM(client=client).ask(system="s", content=[{"type": "text", "text": "x"}],
                                 output_model=GrillTurn, effort="high")  # Sonnet
    assert seen["extra_body"] == {"fallbacks": "default"}
    assert seen["extra_headers"] == {"anthropic-beta": "server-side-fallback-2026-07-01"}


def test_models_can_be_overridden_from_config(monkeypatch):
    from cvmax import config

    monkeypatch.setattr(config, "MODEL", "claude-opus-5-5")
    monkeypatch.setattr(config, "MODEL_LIGHT", "claude-sonnet-5-5")
    seen, client = _capture()
    ClaudeLLM(client=client).ask(system="s", content=[{"type": "text", "text": "x"}],
                                 output_model=GrillTurn, effort="low")
    assert seen["model"] == "claude-sonnet-5-5" and seen["extra_body"] == {"fallbacks": "default"}
