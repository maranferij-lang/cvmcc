"""Turning a finished CV text into a structure for layout (PDF, DOCX).

The model rewrites nothing here: it only arranges the text into sections. After that the code checks
that every item is in the original text almost verbatim, and returns a list of those that are not.
"""

from __future__ import annotations

import difflib
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from . import config, cv_render
from .llm import ask_structured
from .safe_text import defang
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
        content=[{"type": "text", "text": f"<cv>\n{defang(cv_text)}\n</cv>"}],
        output_model=BuiltCV,
        effort=config.EFFORT_GRILL,
    )


MAX_BULLETS = 60  # bullets compared per CV; the rest are not checked (a CV has far fewer)
MAX_SOURCE_CHARS = 40_000  # the part of the source text used for matching
MAX_BULLET_CHARS = 600  # the part of one bullet used for matching
_TOP_WINDOWS = 3  # candidate places in the source that get the (expensive) character-level comparison


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[•▪\-–—]", " ", text).lower().split())


def _candidate_starts(words: list[str], source_words: list[str]) -> list[int]:
    """Word positions in the source whose window shares the most words with the bullet. One linear pass."""
    need = Counter(words)
    have: Counter = Counter()
    score, w = 0, len(words)
    scored: list[tuple[int, int]] = []
    for i, word in enumerate(source_words):
        if word in need:
            if have[word] < need[word]:
                score += 1
            have[word] += 1
        if i >= w:
            old = source_words[i - w]
            if old in need:
                have[old] -= 1
                if have[old] < need[old]:
                    score -= 1
        if i >= w - 1:
            scored.append((score, i - w + 1))
    scored.sort(key=lambda t: -t[0])
    return [start for sc, start in scored[:_TOP_WINDOWS] if sc * 2 >= w]


def changed_bullets(cv_text: str, cv: BuiltCV, threshold: float = 0.92) -> list[str]:
    """Items that are not in the original text almost verbatim. An empty list = nothing was changed.

    The work is bounded: at most MAX_BULLETS bullets of MAX_BULLET_CHARS against MAX_SOURCE_CHARS of source. A word-level
    pass finds the few places in the source that look like the bullet; only those get the character-level comparison.
    """
    source = _norm(cv_text[:MAX_SOURCE_CHARS])
    source_words = source.split()
    offsets, pos = [], 0
    for word in source_words:
        pos = source.index(word, pos)
        offsets.append(pos)
        pos += len(word)
    bullets = [d for e in cv.education for d in e.details]
    for group in (cv.experience, cv.projects, cv.activities):
        bullets += [b for e in group for b in e.bullets]
    bad = []
    for bullet in bullets[:MAX_BULLETS]:
        b = _norm(bullet)[:MAX_BULLET_CHARS]
        if not b or b in source:
            continue
        n = len(b)
        best = 0.0
        for start in _candidate_starts(b.split(), source_words):
            for s0 in (start - 1, start, start + 1):  # the first word may be the changed one
                if 0 <= s0 < len(offsets):
                    cs = offsets[s0]
                    best = max(best, difflib.SequenceMatcher(None, b, source[cs : cs + n]).ratio())
        if best < threshold:
            bad.append(bullet)
    return bad


@dataclass
class FormattedCV:
    """A formatted CV kept in the session: the changed lines are computed once, and the rendered files and preview
    images are made once, so a rerun of the page does not repeat any of it."""

    text: str
    built: BuiltCV
    changed: list[str]
    pdf: bytes | None = None
    docx: bytes | None = None
    pages: list[bytes] | None = None

    def render(self) -> "FormattedCV":
        if self.pdf is None:
            self.pdf = cv_render.render_pdf(self.built)
            self.docx = cv_render.render_docx(self.built)
            self.pages = cv_render.pdf_preview(self.pdf)
        return self


def format_cv(llm: Any, text: str) -> FormattedCV:
    built = structure_cv(llm, text)
    return FormattedCV(text=text, built=built, changed=changed_bullets(text, built))
