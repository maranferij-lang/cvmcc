"""Builder: a CV from scratch using a form, a free-form story and a short interview."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import config
from .grill import QA, GrillSession, next_question_instruction
from .llm import ask_structured
from .profile import PROGRAMS
from .prompts import load_rubric, today
from .schemas import BuiltCV, GrillTurn


@dataclass
class BuilderDraft:
    full_name: str
    email: str
    phone: str
    city: str
    links: str  # LinkedIn, GitHub, portfolio
    program: str
    status: str
    grad_year: str
    gpa: str
    target_role: str
    notes: str  # a free-form story about experience
    feedback_language: str

    def to_prompt(self) -> str:
        return (
            "<candidate>\n"
            f"Full name: {self.full_name}\nEmail: {self.email}\nPhone: {self.phone or '(none)'}\n"
            f"City: {self.city or '(none)'}\nLinks: {self.links or '(none)'}\n"
            f"Field of study: {PROGRAMS.get(self.program, self.program)}\n"
            f"Study status: {self.status}; expected graduation: {self.grad_year or '(unknown)'}; "
            f"GPA: {self.gpa or '(not given)'}\n"
            f"Target role: {self.target_role or '(not decided)'}\n"
            f"<notes_in_own_words>\n{self.notes.strip() or '(empty)'}\n</notes_in_own_words>\n"
            "</candidate>"
        )


def _base(draft: BuilderDraft) -> str:
    return f"""You are GetCVmax, a career coach helping a university student write their first strong CV in English
Today is {today()}.
for internships and entry-level roles at international companies.
The candidate may write in Ukrainian or English. Write every question and note in {draft.feedback_language}. In Ukrainian, address the student informally with «ти», never «ви».
Write all CV content in English.

Never invent anything: no employers, dates, numbers, tools, links, awards or results the candidate did not
state. When a number would make a bullet stronger and it is unknown, use a placeholder like "[X]" or
"[N] people". Keep only what is true.

Rubric of what a strong student CV looks like:

{load_rubric(draft.program)}"""


def builder_question_system(draft: BuilderDraft, max_questions: int) -> str:
    return _base(draft) + f"""

You are interviewing the candidate to collect material for their CV. Ask exactly one question per turn,
about one thing, in at most two short sentences, with no greeting or recap.
Prioritise what the CV needs most: experience or projects with concrete results and numbers, tools used,
student activities and roles, volunteering, competitions, awards, language levels with certificates.
Build on previous answers. If an answer does not give the requested facts (a "no", a skip, or a vague
or evasive reply), drop that topic for good. Never ask twice about the same item or for facts already given.
At most {max_questions} questions in total. Set done=true when you have enough material."""


def builder_build_system(draft: BuilderDraft) -> str:
    return _base(draft) + """

Write the complete CV from everything the candidate told you:
- One page for a student: pick the most relevant items, reverse chronological order.
- contact_line: city and country, email, phone, links, exactly as given.
- summary: one or two specific lines tailored to the target role, or empty if there is not enough material.
- Bullets: strong action verb + what + how + result, 1-2 lines each, 2-4 per entry, past tense for past roles.
- Group skills into categories (e.g. "Data & Analytics", "Tools", "Programming"); only skills the candidate named.
- languages: like "English: C1 (IELTS 7.5)" or "Ukrainian: Native".
- Education details (coursework, thesis, honors, GPA) only as the candidate stated them. Do not add
  typical courses of the program. Do not pad entries with filler bullets that restate the title
  ("Completed course modules..."); fewer true bullets are better.
- Leave a section as an empty list when there is nothing true to put in it.
- notes_for_user: 3-6 short notes in the candidate's language about placeholders to fill, facts to verify,
  and the single most valuable thing to add next."""


def next_builder_question(llm: Any, draft: BuilderDraft, session: GrillSession) -> QA | None:
    if session.finished or session.pending:
        return session.pending
    if len(session.turns) >= session.max_questions:
        session.finished = True
        return None
    content = [{"type": "text", "text": (
        f"{draft.to_prompt()}\n\n<interview_so_far>\n{session.transcript()}\n</interview_so_far>\n\n"
        f"{next_question_instruction(session)}"
    )}]
    turn = ask_structured(llm, system=builder_question_system(draft, session.max_questions),
                          content=content, output_model=GrillTurn, effort=config.EFFORT_GRILL)
    if turn.done or not turn.question.strip():
        session.finished = True
        return None
    qa = QA(question=turn.question.strip(), why_asking=turn.why_asking.strip())
    session.turns.append(qa)
    return qa


def build_cv(llm: Any, draft: BuilderDraft, session: GrillSession) -> BuiltCV:
    session.finished = True
    content = [{"type": "text", "text": (
        f"{draft.to_prompt()}\n\n<interview>\n{session.transcript()}\n</interview>\n\nWrite the CV."
    )}]
    return ask_structured(llm, system=builder_build_system(draft), content=content,
                          output_model=BuiltCV, effort=config.EFFORT_ANALYSIS)
