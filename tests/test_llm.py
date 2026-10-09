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
