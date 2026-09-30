"""Тонка обгортка над Claude API: один виклик, структурована відповідь."""

from __future__ import annotations

from typing import Any, Type, TypeVar

import anthropic
from pydantic import BaseModel

from . import config

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Помилка, яку можна показати юзеру зрозумілим текстом."""


def make_client(api_key: str | None = None) -> anthropic.Anthropic:
    # Без api_key SDK сам бере ANTHROPIC_API_KEY із середовища.
    return anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()


def ask_structured(
    client: Any,
    *,
    system: str,
    content: list[dict],
    output_model: Type[T],
    effort: str,
) -> T:
    """Надсилає один запит і повертає відповідь, перевірену за pydantic-схемою."""
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
        response = client.messages.parse(**kwargs)
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
