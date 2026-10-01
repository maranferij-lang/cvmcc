"""Режим Grill me: модель ставить питання по одному, потім перетворює відповіді на правки."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import config
from .cv_input import CVFile
from .llm import ask_structured
from .profile import Profile
from .prompts import grill_finalize_system, grill_system
from .schemas import GrillResult, GrillTurn


@dataclass
class QA:
    question: str
    why_asking: str
    answer: str = ""


@dataclass
class GrillSession:
    turns: list[QA] = field(default_factory=list)
    finished: bool = False
    max_questions: int = config.GRILL_MAX_QUESTIONS

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
    # Кожен запит самодостатній: CV + профіль + вся розмова. Так простіше й надійніше за багатоходовий чат.
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
    """Питає наступне питання або закінчує сесію. Повертає нове питання чи None."""
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
    qa = QA(question=turn.question.strip(), why_asking=turn.why_asking.strip())
    session.turns.append(qa)
    return qa


def next_question_instruction(session: GrillSession) -> str:
    """Інструкція на кожен хід. Список уже обговорених пунктів тут, а не лише в системному промпті:
    менші моделі так краще не повертаються до тієї самої теми."""
    lines = [f"Questions asked so far: {len(session.turns)} of {session.max_questions}."]
    # Питання чергуються: знайти нове (парні ходи) і уточнити наявне (непарні).
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


def finalize(client: Any, profile: Profile, cv: CVFile, session: GrillSession) -> GrillResult:
    session.finished = True
    return ask_structured(
        client,
        system=grill_finalize_system(profile),
        content=_content(profile, cv, session, "Turn the interview into CV improvements."),
        output_model=GrillResult,
        effort=config.EFFORT_ANALYSIS,
    )
