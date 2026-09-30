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


class GeminiLLM:
    provider = "Google Gemini API"

    def __init__(self, api_key: str | None = None, models: list[str] | None = None) -> None:
        from google import genai

        self.client = genai.Client(api_key=api_key) if api_key else genai.Client()
        self.models = models or config.GEMINI_MODELS

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
            result = self._try_models(parts, cfg, output_model)
            if isinstance(result, Exception):
                last_error = result
                continue
            return result

        if last_error is not None and getattr(last_error, "code", None) == 429:
            raise LLMError("Вичерпано безплатний ліміт Gemini. Спробуй за хвилину або завтра.") from last_error
        raise LLMError("Усі моделі Gemini зараз перевантажені. Спробуй за хвилину.") from last_error

    def _try_models(self, parts: list[Any], cfg: Any, output_model: Type[T]) -> T | Exception:
        """Повертає відповідь, або останню тимчасову помилку, якщо всі моделі зайняті."""
        from google.genai import errors

        last_error: Exception = LLMError("no models configured")
        for model in self.models:
            try:
                response = self.client.models.generate_content(model=model, contents=parts, config=cfg)
            except errors.APIError as e:
                last_error = e
                if e.code in _GEMINI_TRY_NEXT:
                    continue
                if e.code in (401, 403) or "API key" in str(e):
                    raise LLMError("Невірний ключ Gemini. Перевір GEMINI_API_KEY.") from e
                raise LLMError(f"Gemini відхилив запит ({e.code}): {e.message}") from e
            except Exception as e:  # мережа
                raise LLMError("Немає зв'язку з Gemini API. Перевір інтернет.") from e

            parsed = response.parsed
            if isinstance(parsed, output_model):
                return parsed
            if response.text:
                try:
                    return output_model.model_validate_json(response.text)
                except ValueError:
                    pass
            reason = response.candidates[0].finish_reason if response.candidates else "no candidates"
            raise LLMError(f"Gemini повернув відповідь у неочікуваному форматі ({reason}). Спробуй ще раз.")
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
            raise LLMError("Невірний API-ключ. Перевір ANTHROPIC_API_KEY.") from e
        except anthropic.RateLimitError as e:
            raise LLMError("Забагато запитів. Спробуй ще раз за хвилину.") from e
        except anthropic.BadRequestError as e:
            raise LLMError(f"Запит відхилено: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("Немає зв'язку з API. Перевір інтернет.") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"Помилка API ({e.status_code}). Спробуй пізніше.") from e

        if response.stop_reason == "refusal":
            raise LLMError("Модель відмовилась обробити цей запит. Спробуй змінити текст.")
        if response.stop_reason == "max_tokens":
            raise LLMError("Відповідь вийшла задовгою й обрізалась. Спробуй ще раз.")
        if response.parsed_output is None:
            raise LLMError("Модель повернула відповідь у неочікуваному форматі.")
        return response.parsed_output


# ---------------- Вибір провайдера ----------------


def make_llm(gemini_key: str | None = None, anthropic_key: str | None = None) -> GeminiLLM | ClaudeLLM | None:
    """Повертає клієнт потрібного провайдера або None, якщо ключів немає (демо-режим)."""
    gemini_key = gemini_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    anthropic_key = anthropic_key or os.environ.get("ANTHROPIC_API_KEY")
    choice = config.PROVIDER
    if choice == "gemini" or (choice == "auto" and gemini_key):
        if not gemini_key:
            raise LLMError("CVMAX_PROVIDER=gemini, але GEMINI_API_KEY не задано.")
        return GeminiLLM(api_key=gemini_key)
    if choice == "claude" or (choice == "auto" and anthropic_key):
        if not anthropic_key:
            raise LLMError("CVMAX_PROVIDER=claude, але ANTHROPIC_API_KEY не задано.")
        return ClaudeLLM(api_key=anthropic_key)
    return None


def ask_structured(llm: Any, *, system: str, content: list[dict], output_model: Type[T], effort: str) -> T:
    """Надсилає один запит і повертає відповідь, перевірену за pydantic-схемою."""
    if not hasattr(llm, "ask"):
        # Сирий клієнт у стилі Anthropic SDK (напр. фейковий клієнт у демо й тестах).
        llm = ClaudeLLM(client=llm)
    return llm.ask(system=system, content=content, output_model=output_model, effort=effort)
