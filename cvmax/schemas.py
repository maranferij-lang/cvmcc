"""Format of model responses. The API guarantees that the response matches these schemas."""

from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel

Priority = Literal["high", "medium", "low"]


class Edit(BaseModel):
    section: str
    before: str  # An exact quote from the CV. Empty if this is a new item.
    after: str  # New text in English. Empty if the item should be removed.
    reason: str
    priority: Priority


class CriterionScore(BaseModel):
    criterion: str
    score: int  # 1..5
    comment: str


class Gap(BaseModel):
    item: str  # e.g. "SQL (joins, window functions)" or "IELTS 7.0+"
    why_it_matters: str
    how_to_close: str
    time_estimate: str
    impact: Priority


Verdict = Literal["keep", "cut", "shorten", "rewrite", "move"]


class LineVerdict(BaseModel):
    line: str  # a CV line or item, shortened to the first words
    verdict: Verdict
    reason: str


class Analysis(BaseModel):
    overall_score: int  # 0..100
    summary: str
    target_assumptions: List[str]
    scores: List[CriterionScore]
    strengths: List[str]
    line_review: List[LineVerdict]  # a verdict for each CV line, before the edits
    edits: List[Edit]
    gaps: List[Gap]
    missing_info: List[str]


class GrillTurn(BaseModel):
    done: bool
    kind: Literal["discover", "deepen"]
    question: str
    why_asking: str


class GrillResult(BaseModel):
    new_facts: List[str]
    edits: List[Edit]


class Direction(BaseModel):
    role: str  # role title in English, as in job postings
    company_type: str  # one value from the list profile.COMPANY_TYPES
    fit_score: int  # 0..100: a realistic chance of getting an offer in the near future
    why_fits: List[str]  # evidence from the CV
    gaps: List[str]
    first_steps: List[str]
    search_keywords: List[str]


class CareerMatch(BaseModel):
    candidate_summary: str
    strongest_assets: List[str]
    directions: List[Direction]
    general_advice: str


class CVEntry(BaseModel):
    title: str
    organization: str
    location: str
    dates: str
    bullets: List[str]


class CVEducation(BaseModel):
    institution: str
    degree: str
    location: str
    dates: str
    details: List[str]


class CVSkillGroup(BaseModel):
    category: str
    items: List[str]


class BuiltCV(BaseModel):
    full_name: str
    contact_line: List[str]
    summary: str
    education: List[CVEducation]
    experience: List[CVEntry]
    projects: List[CVEntry]
    activities: List[CVEntry]
    skills: List[CVSkillGroup]
    languages: List[str]
    awards: List[str]
    notes_for_user: List[str]


class JobFit(BaseModel):
    id: str
    fit: int  # 0..100, clipped in rank_jobs
    why: str
    missing: List[str]
    apply_now: bool


class RankedJobs(BaseModel):
    items: List[JobFit]


class EditVerdict(BaseModel):
    index: int  # номер правки зі списку в запиті
    ok: bool  # false, якщо after додає факти, яких немає в CV, або описує іншу діяльність
    problem: str  # коротко, що не так (мовою відповіді); порожньо, якщо ok
    fixed_after: str  # after без вигаданої частини або з нею в [дужках]; порожньо, якщо виправити не можна


class EditVerdicts(BaseModel):
    items: List[EditVerdict]
