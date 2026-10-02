"""Виклик моделі: один запит, структурована відповідь за pydantic-схемою.

Підтримуються два провайдери: Gemini і Claude. Решта коду про це не знає:
вона передає CV і текст у форматі блоків і отримує готовий pydantic-об'єкт.
"""

from __future__ import annotations

import base64
import os
import time
from typing import Any, Type, TypeVar

from pydantic import BaseModel

from . import config

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Помилка, яку можна показати юзеру зрозумілим текстом."""


# ---------------- Gemini ----------------

_GEMINI_THINKING = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}
# Коди, за яких є сенс спробувати наступну модель.
_GEMINI_TRY_NEXT = {429, 500, 503, 504}
RETRY_PAUSE_SECONDS = 5


def _pacific_day() -> str:
    """Денні ліміти Gemini скидаються опівночі за тихоокеанським часом."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()


# Моделі, які сьогодні вже вичерпали денний ліміт: не витрачаємо на них час до скидання.
_EXHAUSTED: dict[str, str] = {}


def _is_daily_quota(error: Any) -> bool:
    text = str(error)
    return getattr(error, "code", None) == 429 and ("PerDay" in text or "per day" in text.lower())


class GeminiLLM:
    provider = "Google Gemini API"

    def __init__(
        self,
        api_key: str | None = None,
        models: list[str] | None = None,
        light_models: list[str] | None = None,
    ) -> None:
        from google import genai
        from google.genai import types

        # Власні повтори SDK вимкнено: ми самі переходимо на іншу модель, так швидше.
        http = types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1), timeout=180_000)
        self.client = genai.Client(api_key=api_key, http_options=http) if api_key else genai.Client(http_options=http)
        self.models = models or config.GEMINI_MODELS_HEAVY
        self.light_models = light_models or config.GEMINI_MODELS_LIGHT

    def _chain(self, effort: str) -> list[str]:
        chain = self.light_models if effort == "low" else self.models
        today = _pacific_day()
        available = [m for m in chain if _EXHAUSTED.get(m) != today]
        return available or chain

    @staticmethod
    def _parts(content: list[dict]) -> list[Any]:
        from google.genai import types

        parts = []
        for block in content:
            if block["type"] == "text":
                parts.append(types.Part.from_text(text=block["text"]))
            elif block["type"] == "document":
                src = block["source"]
                parts.append(
                    types.Part.from_bytes(data=base64.b64decode(src["data"]), mime_type=src["media_type"])
                )
            else:
                raise ValueError(f"Unsupported block type: {block['type']}")
        return parts

    def ask(self, *, system: str, content: list[dict], output_model: Type[T], effort: str) -> T:
        from google.genai import types

        cfg = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=output_model,
            max_output_tokens=config.MAX_TOKENS * 2,
            thinking_config=types.ThinkingConfig(thinking_level=_GEMINI_THINKING.get(effort, "high")),
        )
        parts = self._parts(content)
        last_error: Exception | None = None
        # Безплатні моделі Gemini часто перевантажені. Два кола по всіх моделях із паузою між ними.
        for round_no in range(2):
            if round_no:
                time.sleep(RETRY_PAUSE_SECONDS)
            result = self._try_models(self._chain(effort), parts, cfg, output_model)
            if isinstance(result, Exception):
                last_error = result
                continue
            return result

        if last_error is not None and getattr(last_error, "code", None) == 429:
            raise LLMError(
                "The free AI model has hit its daily limit. Try again in a minute, and if that fails, tomorrow."
            ) from last_error
        raise LLMError("The free AI models are overloaded right now. Try again in a minute.") from last_error

    def _try_models(self, models: list[str], parts: list[Any], cfg: Any, output_model: Type[T]) -> T | Exception:
        """Повертає відповідь, або останню тимчасову помилку, якщо всі моделі зайняті."""
        from google.genai import errors

        last_error: Exception = LLMError("no models configured")
        for model in models:
            try:
                response = self.client.models.generate_content(model=model, contents=parts, config=cfg)
            except errors.APIError as e:
                last_error = e
                if _is_daily_quota(e):
                    _EXHAUSTED[model] = _pacific_day()
                if e.code in _GEMINI_TRY_NEXT or e.code == 404:
                    continue
                if e.code in (401, 403) or "API key" in str(e):
                    raise LLMError("Invalid Gemini key. Check GEMINI_API_KEY.") from e
                raise LLMError(f"Gemini rejected the request ({e.code}): {e.message}") from e
            except Exception as e:  # мережа
                raise LLMError("Cannot reach the Gemini API. Try again in a minute.") from e

            parsed = response.parsed
            if isinstance(parsed, output_model):
                return parsed
            if response.text:
                try:
                    return output_model.model_validate_json(response.text)
                except ValueError:
                    pass
            reason = response.candidates[0].finish_reason if response.candidates else "no candidates"
            raise LLMError(f"The model returned an unexpected format ({reason}). Please try again.")
        return last_error


# ---------------- Claude ----------------


class ClaudeLLM:
    provider = "Claude API (Anthropic)"

    def __init__(self, api_key: str | None = None, client: Any = None) -> None:
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.client = client

    def ask(self, *, system: str, content: list[dict], output_model: Type[T], effort: str) -> T:
        import anthropic

        kwargs: dict[str, Any] = dict(
            model=config.MODEL,
            max_tokens=config.MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_format=output_model,
            output_config={"effort": effort},
        )
        if config.USE_FALLBACKS:
            kwargs["extra_headers"] = {"anthropic-beta": "server-side-fallback-2026-07-01"}
            kwargs["extra_body"] = {"fallbacks": "default"}

        try:
            response = self.client.messages.parse(**kwargs)
        except anthropic.AuthenticationError as e:
            raise LLMError("Invalid API key. Check ANTHROPIC_API_KEY.") from e
        except anthropic.RateLimitError as e:
            raise LLMError("Too many requests. Try again in a minute.") from e
        except anthropic.BadRequestError as e:
            raise LLMError(f"Request rejected: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("Cannot reach the API. Try again in a minute.") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"API error ({e.status_code}). Try again later.") from e

        if response.stop_reason == "refusal":
            raise LLMError("The model declined this request. Try changing the text.")
        if response.stop_reason == "max_tokens":
            raise LLMError("The answer was too long and got cut off. Please try again.")
        if response.parsed_output is None:
            raise LLMError("The model returned an unexpected format.")
        return response.parsed_output


# ---------------- Вибір провайдера ----------------


def make_llm(gemini_key: str | None = None, anthropic_key: str | None = None) -> GeminiLLM | ClaudeLLM | None:
    """Повертає клієнт потрібного провайдера або None, якщо ключів немає (демо-режим)."""
    gemini_key = gemini_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    anthropic_key = anthropic_key or os.environ.get("ANTHROPIC_API_KEY")
    choice = config.PROVIDER
    if choice == "gemini" or (choice == "auto" and gemini_key):
        if not gemini_key:
            raise LLMError("CVMAX_PROVIDER=gemini, but GEMINI_API_KEY is not set.")
        return GeminiLLM(api_key=gemini_key)
    if choice == "claude" or (choice == "auto" and anthropic_key):
        if not anthropic_key:
            raise LLMError("CVMAX_PROVIDER=claude, but ANTHROPIC_API_KEY is not set.")
        return ClaudeLLM(api_key=anthropic_key)
    return None


MAX_REQUEST_CHARS = 200_000  # промпт із рубриками ~15 тис. символів, CV до 40 тис., решта форми до 15 тис.


def ask_structured(llm: Any, *, system: str, content: list[dict], output_model: Type[T], effort: str) -> T:
    """Надсилає один запит і повертає відповідь, перевірену за pydantic-схемою."""
    size = len(system) + sum(len(b.get("text", "")) for b in content)
    if size > MAX_REQUEST_CHARS:  # захист від гігантських запитів в обхід обмежень форми
        raise LLMError("The request is too large. Shorten the job posting or your answers and try again.")
    if not hasattr(llm, "ask"):
        # Сирий клієнт у стилі Anthropic SDK (напр. фейковий клієнт у демо й тестах).
        llm = ClaudeLLM(client=llm)
    return llm.ask(system=system, content=content, output_model=output_model, effort=effort)
