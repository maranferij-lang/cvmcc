"""Повний аналіз CV під ціль."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from typing import Any

from . import config
from .checks import CheckReport, render_for_prompt, run_checks
from .cv_input import CVFile
from .llm import ask_structured
from .profile import Profile
from .prompts import analysis_system
from .schemas import Analysis
from .scoring import ScoreDetail, compute_score
from .verify import Verification, verify_edits

log = logging.getLogger(__name__)


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


# Теги-розділювачі, якими ми самі збираємо запит. Хто б не вписав їх у CV чи вакансію, це текст кандидата:
# без заміни він міг би закрити свій блок або підробити <checks>, якому модель довіряє.
_REQUEST_TAGS = re.compile(r"<(\s*/?\s*(?:checks|cv|length|candidate_profile|target|vacancy_text)\b)", re.I)
# Вільні поля профілю, які юзер заповнює сам і які йдуть у запит до блоку <checks>.
_PROFILE_FREE_TEXT = ("background", "target_role", "company_details", "vacancy_text")


def _defang(text: str) -> str:
    """Тег-розділювач у тексті кандидата стає «‹tag»: зміст лишається, але це вже не тег."""
    return _REQUEST_TAGS.sub("\u2039\\1", text)


def _request_copies(profile: Profile, cv: CVFile) -> tuple[Profile, CVFile]:
    """Копії профілю й CV для тексту запиту. Перевірки, верифікатор і бал далі працюють з оригіналами."""
    safe_profile = replace(profile, **{f: _defang(getattr(profile, f)) for f in _PROFILE_FREE_TEXT})
    return safe_profile, replace(cv, filename=_defang(cv.filename), text=_defang(cv.text))


@dataclass
class AnalysisRun:
    """Результат одного розбору: відповідь моделі та все, що ми порахували навколо неї."""

    analysis: Analysis  # overall_score уже підсумковий (дорівнює score.score)
    checks: CheckReport | None  # None, якщо перевірки вимкнені або зламалися
    score: ScoreDetail
    verification: Verification | None  # None, якщо верифікатор вимкнений або зламався


def _skipped(what: str, exc: Exception) -> None:
    """Збій допоміжного кроку не ламає розбір. У журнал іде лише тип помилки: у повідомленні може бути текст CV."""
    log.warning("%s failed (%s); continuing without it", what, type(exc).__name__)
    log.debug("%s failure details", what, exc_info=True)


def _run_checks(cv: CVFile, profile: Profile) -> tuple[CheckReport | None, str]:
    """Детерміновані перевірки й блок <checks> для запиту. Без них повертає (None, "")."""
    if not config.CHECKS_ENABLED:
        return None, ""
    try:
        report = run_checks(cv, profile)
        return report, render_for_prompt(report)
    except Exception as exc:
        _skipped("Automatic checks", exc)
        return None, ""


def _verify(client: Any, profile: Profile, cv: CVFile, result: Analysis, facts: str) -> Verification | None:
    """Прибирає або урізає правки з вигаданими фактами. Збій верифікатора лишає правки як є."""
    if not config.VERIFY_ENABLED:
        return None
    known = "\n".join(part for part in (profile.background.strip(), facts.strip()) if part)
    try:
        edits, verification = verify_edits(
            client, cv_text=cv.text, facts=known, edits=result.edits,
            feedback_language=profile.feedback_language,
        )
    except Exception as exc:  # LLMError верифікатора теж: розбір уже готовий і без нього
        _skipped("Edit verification", exc)
        return None
    result.edits = edits
    return verification


def _final_score(result: Analysis, report: CheckReport | None, program: str) -> ScoreDetail:
    """Підсумковий бал. Якщо підрахунок зламався, лишається бал моделі без штрафу."""
    try:
        return compute_score(result, report, program)
    except Exception as exc:
        _skipped("Score computation", exc)
        model_score = result.overall_score
        return ScoreDetail(score=model_score, criteria_score=model_score, model_score=model_score,
                           penalty=0, matched=0, items=[])


def analyze_full(client: Any, profile: Profile, cv: CVFile, addendum: str = "", *, facts: str = "") -> AnalysisRun:
    """Розбір CV: перевірки -> модель -> верифікатор правок -> бал.

    `facts`: додаткові відомі факти про кандидата (наприклад, відповіді Q&A), на додачу до profile.background.
    Помилка самого розбору (LLMError) іде нагору; помилки перевірок, верифікатора й балу лише логуються.
    """
    report, checks_block = _run_checks(cv, profile)
    note = length_note(cv)
    # Єдиний справжній <checks> іде останнім, після <candidate_profile>: його будуємо ми, решта тегів це текст юзера.
    request_profile, request_cv = _request_copies(profile, cv)
    content = request_cv.as_content_blocks() + [
        {"type": "text", "text": request_profile.to_prompt() + (f"\n{note}" if note else "")
         + (f"\n{checks_block}" if checks_block else "") + "\n\nReview this CV for the target above."}
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
    result = drop_noise(result, profile.feedback_language)
    verification = _verify(client, profile, cv, result, facts)
    score = _final_score(result, report, profile.program)
    result.overall_score = score.score  # далі в застосунку скрізь бал стабільний, а не «відчуття» моделі
    return AnalysisRun(analysis=result, checks=report, score=score, verification=verification)


def analyze_cv(client: Any, profile: Profile, cv: CVFile, addendum: str = "") -> Analysis:
    """Старий виклик: лише відповідь розбору (див. analyze_full)."""
    return analyze_full(client, profile, cv, addendum).analysis
