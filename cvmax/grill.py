"""Grill me mode: the model asks questions one at a time, then turns the answers into edits."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from . import config
from .analyze import is_cosmetic
from .cv_input import CVFile
from .llm import ask_structured
from .profile import Profile
from .prompts import grill_finalize_system, grill_system
from .schemas import GrillResult, GrillTurn
from .verify import Verification, verify_edits

log = logging.getLogger(__name__)


@dataclass
class QA:
    question: str
    why_asking: str
    answer: str = ""
    kind: str = ""  # discover / deepen, for the event log


@dataclass
class GrillSession:
    turns: list[QA] = field(default_factory=list)
    finished: bool = False
    max_questions: int = config.GRILL_MAX_QUESTIONS
    verification: Verification | None = None  # підсумок перевірки правок після finalize; None, якщо її не було

    @property
    def pending(self) -> QA | None:
        if self.turns and not self.turns[-1].answer and not self.finished:
            return self.turns[-1]
        return None

    def transcript(self) -> str:
        if not self.turns:
            return "(no questions asked yet)"
        lines = []
        for i, t in enumerate(self.turns, 1):
            lines.append(f"Q{i}: {t.question}\nA{i}: {t.answer or '(no answer)'}")
        return "\n\n".join(lines)


def _content(profile: Profile, cv: CVFile, session: GrillSession, instruction: str) -> list[dict]:
    # Each request is self-contained: CV + profile + the whole conversation. This is simpler and more reliable than a multi-turn chat.
    return cv.as_content_blocks() + [
        {
            "type": "text",
            "text": (
                f"{profile.to_prompt()}\n\n<interview_so_far>\n{session.transcript()}\n</interview_so_far>\n\n"
                f"{instruction}"
            ),
        }
    ]


def next_question(client: Any, profile: Profile, cv: CVFile, session: GrillSession) -> QA | None:
    """Asks the next question or ends the session. Returns the new question or None."""
    if session.finished or session.pending:
        return session.pending
    if len(session.turns) >= session.max_questions:
        session.finished = True
        return None
    turn = ask_structured(
        client,
        system=grill_system(profile, session.max_questions),
        content=_content(profile, cv, session, next_question_instruction(session)),
        output_model=GrillTurn,
        effort=config.EFFORT_GRILL,
    )
    if turn.done or not turn.question.strip():
        session.finished = True
        return None
    qa = QA(question=turn.question.strip(), why_asking=turn.why_asking.strip(), kind=turn.kind)
    session.turns.append(qa)
    return qa


def next_question_instruction(session: GrillSession) -> str:
    """Instruction for every turn. The list of already discussed points is here, not only in the system prompt:
    smaller models are better at not returning to the same topic this way."""
    lines = [f"Questions asked so far: {len(session.turns)} of {session.max_questions}."]
    # Questions alternate: find something new (even turns) and clarify something existing (odd turns).
    if len(session.turns) % 2 == 0:
        lines.append("This turn: ask a DISCOVER question about something the CV does not mention yet.")
    else:
        lines.append("This turn: ask a DEEPEN question that adds numbers or results to an item on the CV.")
    if session.turns:
        lines.append(
            "These CV items are already covered. Do NOT ask about any of them again, in any form, "
            "even if the answer was short or vague:"
        )
        lines += [f"- {t.question}" for t in session.turns]
        lines.append("Pick a different experience, project or section of the CV.")
    lines.append("Ask the next question, or set done=true if nothing important is left.")
    return "\n".join(lines)


def answer(session: GrillSession, text: str) -> None:
    if session.pending is None:
        raise ValueError("Немає питання, на яке можна відповісти.")
    session.turns[-1].answer = text.strip() or "(skipped)"


def _answer_facts(profile: Profile, session: GrillSession) -> str:
    """Факти для верифікатора: відповіді кандидата й опис його досвіду з профілю.

    Питання моделі сюди не йдуть: у них можуть бути числа, яких кандидат не називав.
    """
    answers = [t.answer.strip() for t in session.turns if t.answer.strip() and t.answer.strip() != "(skipped)"]
    return "\n".join([profile.background.strip(), *answers]).strip()


def finalize(client: Any, profile: Profile, cv: CVFile, session: GrillSession, verify: bool = True) -> GrillResult:
    session.finished = True
    result = ask_structured(
        client,
        system=grill_finalize_system(profile),
        content=_content(profile, cv, session, "Turn the interview into CV improvements."),
        output_model=GrillResult,
        effort=config.EFFORT_ANALYSIS,
    )
    # Як і в analyze_full: правки, що міняють лише формат чи дати, не показуємо. Це до верифікатора й незалежно від нього.
    result.edits = [e for e in result.edits if not is_cosmetic(e.before, e.after)]
    session.verification = None
    if verify and config.VERIFY_ENABLED:
        try:
            edits, session.verification = verify_edits(
                client, cv_text=cv.text, facts=_answer_facts(profile, session), edits=result.edits,
                feedback_language=profile.feedback_language,
            )
            result.edits = edits
        except Exception as exc:  # без верифікатора правки лишаються як є, як і в analyze_full
            # У журнал іде лише тип помилки: у повідомленні може бути текст CV чи відповідей.
            log.warning("Edit verification failed (%s); edits were not verified", type(exc).__name__)
            log.debug("Edit verification failure details", exc_info=True)
            session.verification = None
    return result
