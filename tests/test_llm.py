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


# ---------------- The real client streams ----------------

class _Stream:
    def __init__(self, message, seen):
        self.message, self.seen = message, seen

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


def _stream_client(message):
    seen: list[dict] = []

    def stream(**kwargs):
        seen.append(kwargs)
        return _Stream(message, seen)

    return seen, SimpleNamespace(messages=SimpleNamespace(stream=stream, parse=None))


def _text_message(text, stop_reason="end_turn"):
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)])


GOOD = '{"done": false, "kind": "discover", "question": "What changed?", "why_asking": "numbers"}'


def test_stream_path_validates_json_after_the_stop_reason():
    seen, client = _stream_client(_text_message(GOOD))
    turn = _ask(client)
    assert isinstance(turn, GrillTurn) and turn.question == "What changed?"
    body = seen[0]
    assert "output_format" not in body
    assert body["max_tokens"] >= 32000
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["format"]["schema"]["additionalProperties"] is False


def test_cut_off_answer_says_too_long_not_wrong_format():
    _, client = _stream_client(_text_message(GOOD[:30], stop_reason="max_tokens"))
    with pytest.raises(LLMError, match="too long"):
        _ask(client)


def test_stream_schema_error_hides_the_model_text():
    bad = '{"done": "maybe", "kind": "%s", "question": 1}' % SECRET
    _, client = _stream_client(_text_message(bad))
    with pytest.raises(LLMError) as exc:
        _ask(client)
    assert SECRET not in str(exc.value) and "expected format" in str(exc.value)


def test_stream_refusal_and_empty_answer():
    _, client = _stream_client(_text_message("", stop_reason="refusal"))
    with pytest.raises(LLMError, match="declined"):
        _ask(client)
    _, client = _stream_client(SimpleNamespace(stop_reason="end_turn", content=[]))
    with pytest.raises(LLMError, match="unexpected format"):
        _ask(client)


class _DroppedStream:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        raise ConnectionResetError("peer closed connection without sending complete message body")


def test_dropped_stream_is_retried_once_then_a_soft_error():
    calls = []

    def stream(**kwargs):
        calls.append(1)
        return _DroppedStream()

    client = SimpleNamespace(messages=SimpleNamespace(stream=stream, parse=None))
    with pytest.raises(LLMError, match="connection dropped"):
        _ask(client)
    assert len(calls) == 2


def test_dropped_stream_recovers_on_the_second_attempt():
    attempts = iter([_DroppedStream(), _Stream(_text_message(GOOD), [])])
    client = SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: next(attempts), parse=None))
    assert _ask(client).question == "What changed?"
