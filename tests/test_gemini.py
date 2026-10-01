from types import SimpleNamespace

import pytest
from google.genai import errors

from cvmax import llm as llm_mod
from cvmax.llm import GeminiLLM, LLMError, make_llm
from cvmax.schemas import GrillTurn

OK = GrillTurn(done=False, kind="deepen", question="Q?", why_asking="why")


def busy():
    return errors.ServerError(503, {"error": {"code": 503, "message": "busy", "status": "UNAVAILABLE"}})


def make(responses, models=("m1", "m2")):
    """GeminiLLM із фейковим клієнтом, що віддає відповіді по черзі."""
    calls = []

    def generate_content(model, contents, config):
        calls.append(model)
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return SimpleNamespace(parsed=r, text=None, candidates=[])

    g = GeminiLLM.__new__(GeminiLLM)
    g.client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    g.models = list(models)
    g.light_models = list(models)
    llm_mod._EXHAUSTED.clear()
    return g, calls


def ask(g):
    blocks = [
        {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "JVBERg=="}},
        {"type": "text", "text": "hi"},
    ]
    return g.ask(system="s", content=blocks, output_model=GrillTurn, effort="low")


def test_falls_back_to_next_model(monkeypatch):
    g, calls = make([busy(), OK])
    assert ask(g) == OK and calls == ["m1", "m2"]


def test_second_round_after_pause(monkeypatch):
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    g, calls = make([busy(), busy(), OK])
    assert ask(g) == OK and calls == ["m1", "m2", "m1"]


def test_all_busy_gives_friendly_error(monkeypatch):
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    g, _ = make([busy()] * 4)
    with pytest.raises(LLMError, match="перевантажені"):
        ask(g)


def test_bad_request_is_not_retried():
    bad = errors.ClientError(400, {"error": {"code": 400, "message": "bad", "status": "INVALID_ARGUMENT"}})
    g, calls = make([bad, OK])
    with pytest.raises(LLMError, match="400"):
        ask(g)
    assert calls == ["m1"]


def test_provider_choice(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert make_llm() is None
    assert isinstance(make_llm(gemini_key="k", anthropic_key="a"), GeminiLLM)
    assert make_llm(anthropic_key="a").provider.startswith("Claude")


def daily_quota():
    return errors.ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
        "message": "Quota exceeded: GenerateRequestsPerDayPerProjectPerModel-FreeTier"}})


def test_daily_quota_model_is_skipped_until_reset(monkeypatch):
    g, calls = make([daily_quota(), OK, OK])
    assert ask(g) == OK and calls == ["m1", "m2"]
    assert ask(g) == OK and calls == ["m1", "m2", "m2"]  # m1 більше не пробуємо сьогодні
    monkeypatch.setattr(llm_mod, "_pacific_day", lambda: "2099-01-01")
    g2, calls2 = make([OK])
    llm_mod._EXHAUSTED["m1"] = "2000-01-01"
    assert ask(g2) == OK and calls2 == ["m1"]  # новий день, модель знову доступна


def test_light_tasks_use_light_chain():
    g, calls = make([OK])
    g.light_models = ["lite"]
    blocks = [{"type": "text", "text": "hi"}]
    g.ask(system="s", content=blocks, output_model=GrillTurn, effort="low")
    assert calls == ["lite"]
