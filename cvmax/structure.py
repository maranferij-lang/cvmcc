"""Перетворення готового тексту CV на структуру для оформлення (PDF, DOCX).

Модель тут нічого не переписує: вона лише розкладає текст по розділах. Після цього код перевіряє,
що кожен пункт є в початковому тексті майже дослівно, і повертає список тих, яких немає.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from . import config
from .llm import ask_structured
from .schemas import BuiltCV

SYSTEM = """You convert a finished CV from plain text into a structure for typesetting.
Do not rewrite, shorten, translate, merge or improve anything. Copy every bullet, title, organisation,
location and date exactly as written, character for character, only fixing obvious line-break artifacts
(a word or line split in two). Keep the original order.

Mapping:
- full_name: the name. contact_line: each contact item separately (phone, email, city, links).
- summary: a summary paragraph if the CV has one, else empty.
- education: one entry per degree; details are its bullets.
- experience: jobs and internships. title = role, organization = employer, location, dates.
- projects: projects; title = project name, organization empty unless stated.
- activities: leadership, competitions, volunteering.
- skills: each line of an "additional information" or "skills" block as a group, category = its label
  ("Technical", "Certifications", "Interests"...), items = the comma-separated parts.
- languages: the languages line split into items like "Ukrainian (Native)".
- awards: awards if there is a separate section.
- notes_for_user: empty list."""


def structure_cv(llm: Any, cv_text: str) -> BuiltCV:
    return ask_structured(
        llm,
        system=SYSTEM,
        content=[{"type": "text", "text": f"<cv>\n{cv_text}\n</cv>"}],
        output_model=BuiltCV,
        effort=config.EFFORT_GRILL,
    )


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[•▪\-–—]", " ", text).lower().split())


def changed_bullets(cv_text: str, cv: BuiltCV, threshold: float = 0.92) -> list[str]:
    """Пункти, яких немає в початковому тексті майже дослівно. Порожній список = нічого не змінено."""
    source = _norm(cv_text)
    bullets = [d for e in cv.education for d in e.details]
    for group in (cv.experience, cv.projects, cv.activities):
        bullets += [b for e in group for b in e.bullets]
    bad = []
    for bullet in bullets:
        b = _norm(bullet)
        if not b or b in source:
            continue
        # Шукаємо найсхожіший шматок такої ж довжини.
        n = len(b)
        best = max(
            (difflib.SequenceMatcher(None, b, source[i : i + n]).ratio() for i in range(0, max(1, len(source) - n + 1), 20)),
            default=0.0,
        )
        if best < threshold:
            bad.append(bullet)
    return bad
