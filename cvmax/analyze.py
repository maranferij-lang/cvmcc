"""Повний аналіз CV під ціль."""

from __future__ import annotations

from typing import Any

from . import config
from .cv_input import CVFile
from .llm import ask_structured
from .profile import Profile
from .prompts import analysis_system
from .schemas import Analysis


def analyze_cv(client: Any, profile: Profile, cv: CVFile) -> Analysis:
    content = cv.as_content_blocks() + [
        {"type": "text", "text": profile.to_prompt() + "\n\nReview this CV for the target above."}
    ]
    result = ask_structured(
        client,
        system=analysis_system(profile),
        content=content,
        output_model=Analysis,
        effort=config.EFFORT_ANALYSIS,
    )
    # Схема не обмежує діапазони чисел, тому підрізаємо тут.
    result.overall_score = max(0, min(100, result.overall_score))
    for s in result.scores:
        s.score = max(1, min(5, s.score))
    return result
