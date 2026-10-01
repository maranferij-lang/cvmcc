"""Повний аналіз CV під ціль."""

from __future__ import annotations

from typing import Any

from . import config
from .cv_input import CVFile
from .llm import ask_structured
from .profile import Profile
from .prompts import analysis_system
from .schemas import Analysis


ONE_PAGE_WORDS = 650  # приблизно стільки влазить на сторінку A4 щільного студентського CV


def length_note(cv: CVFile) -> str:
    """Підказка моделі про довжину: скільки треба скоротити, щоб влізти на одну сторінку."""
    words = cv.word_count
    if words == 0:
        return ""
    pages = f"{cv.pages} page(s), " if cv.pages else ""
    if (cv.pages or 1) <= 1 and words <= ONE_PAGE_WORDS:
        return f"<length>{pages}~{words} words. It fits on one page; cut only what is weak.</length>"
    excess = max(words - (ONE_PAGE_WORDS - 50), 60)
    return (
        f"<length>{pages}~{words} words. A student or early-career CV must fit on ONE page "
        f"(about {ONE_PAGE_WORDS} words). Propose cuts or shortenings that remove about {excess} words, "
        "starting with the lines that matter least for this target, and in each reason say why that line "
        "goes before the others.</length>"
    )


def analyze_cv(client: Any, profile: Profile, cv: CVFile) -> Analysis:
    note = length_note(cv)
    content = cv.as_content_blocks() + [
        {"type": "text", "text": profile.to_prompt() + (f"\n{note}" if note else "")
         + "\n\nReview this CV for the target above."}
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
