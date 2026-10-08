"""Повний аналіз CV під ціль."""

from __future__ import annotations

import re
from typing import Any

from . import config
from .cv_input import CVFile
from .llm import ask_structured
from .profile import Profile
from .prompts import analysis_system
from .schemas import Analysis


ONE_PAGE_WORDS = 650  # приблизно стільки влазить на сторінку A4 щільного студентського CV

# Поради поза CV, які модель іноді дає попри заборону: вони корисні всім і нічого не кажуть про це CV.
_OFF_TOPIC_GAP = re.compile(
    r"network|coffee|informational interview|referral|mentor|linkedin (activity|posts|connections)|"
    r"apply to more|soft skill|нетворк|кава",
    re.I,
)
_MONTHS = {m[:3]: m for m in ("january", "february", "march", "april", "may", "june", "july", "august",
                               "september", "october", "november", "december")}


def _skeleton(text: str) -> str:
    """Текст без форматування: лише слова й числа, місяці в повній формі."""
    words = re.findall(r"[a-zа-яіїєґ]+|\d+", text.lower())
    return " ".join(_MONTHS.get(w[:3], w) if w[:3] in _MONTHS and len(w) <= 9 else w for w in words)


_DATE_WORDS = re.compile(r"\b(\d{4}|\d{1,2}|(%s)[a-z]*|expected|present|current|now|graduation)\b" % "|".join(_MONTHS), re.I)


def _no_dates(text: str) -> str:
    text = re.sub(r"\[[^\]]*\?\]", " ", text)  # «[Sep 2024?]» і подібні питання моделі
    return " ".join(_DATE_WORDS.sub(" ", _skeleton(text)).split())


def is_cosmetic(before: str, after: str) -> bool:
    """Правка змінює лише формат або дати (тире, регістр, пробіли, роки), а не зміст.

    Дати модель «виправляє» найчастіше, бо не знає сьогоднішнього дня. Їх юзер знає краще.
    """
    if not (before.strip() and after.strip()):
        return False
    return _skeleton(before) == _skeleton(after) or _no_dates(before) == _no_dates(after)


_ALREADY = {
    "English": "If you already use it, just add it to your CV (see Edits). If not: ",
    "Ukrainian": "Якщо вже користуєшся, просто додай у CV (див. «Правки»). Якщо ні: ",
}


def drop_noise(result: Analysis, language: str = "English") -> Analysis:
    """Прибирає косметичні правки й поради, які не стосуються CV."""
    cosmetic = [e for e in result.edits if is_cosmetic(e.before, e.after)]
    result.edits = [e for e in result.edits if e not in cosmetic]
    gone = {e.before.strip() for e in cosmetic}
    result.line_review = [v for v in result.line_review
                          if not (v.verdict == "rewrite" and any(v.line.strip()[:40] in b for b in gone))]
    result.gaps = [g for g in result.gaps if not _OFF_TOPIC_GAP.search(f"{g.item} {g.how_to_close}")]
    # Навичка, про яку правка вже питає «[SQL?]», можливо, просто не вписана: не кажемо «вивчи».
    asked = " ".join(re.findall(r"\[([^\]]*\?)\]", " ".join(e.after for e in result.edits))).lower()
    prefix = _ALREADY.get(language, _ALREADY["English"])
    for g in result.gaps:
        name = re.split(r"[\s(:/,]", g.item.strip(), maxsplit=1)[0].lower()
        if name and len(name) > 1 and name in asked and not g.how_to_close.startswith(prefix[:12]):
            g.how_to_close = prefix + g.how_to_close
    return result


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


def analyze_cv(client: Any, profile: Profile, cv: CVFile, addendum: str = "") -> Analysis:
    note = length_note(cv)
    content = cv.as_content_blocks() + [
        {"type": "text", "text": profile.to_prompt() + (f"\n{note}" if note else "")
         + "\n\nReview this CV for the target above."}
    ]
    result = ask_structured(
        client,
        system=analysis_system(profile, addendum),
        content=content,
        output_model=Analysis,
        effort=config.EFFORT_ANALYSIS,
    )
    # Схема не обмежує діапазони чисел, тому підрізаємо тут.
    result.overall_score = max(0, min(100, result.overall_score))
    for s in result.scores:
        s.score = max(1, min(5, s.score))
    return drop_noise(result, profile.feedback_language)
