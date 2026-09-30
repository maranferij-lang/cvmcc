"""Формат відповідей моделі. API гарантує, що відповідь відповідає цим схемам."""

from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel

Priority = Literal["high", "medium", "low"]


class Edit(BaseModel):
    section: str
    before: str  # Точна цитата з CV. Порожньо, якщо це новий пункт.
    after: str  # Новий текст англійською. Порожньо, якщо пункт треба прибрати.
    reason: str
    priority: Priority


class CriterionScore(BaseModel):
    criterion: str
    score: int  # 1..5
    comment: str


class Gap(BaseModel):
    item: str  # напр. "SQL (joins, window functions)" або "IELTS 7.0+"
    why_it_matters: str
    how_to_close: str
    time_estimate: str
    impact: Priority


class Analysis(BaseModel):
    overall_score: int  # 0..100
    summary: str
    target_assumptions: List[str]
    scores: List[CriterionScore]
    strengths: List[str]
    edits: List[Edit]
    gaps: List[Gap]
    missing_info: List[str]


class GrillTurn(BaseModel):
    done: bool
    question: str
    why_asking: str


class GrillResult(BaseModel):
    new_facts: List[str]
    edits: List[Edit]


class Direction(BaseModel):
    role: str  # назва ролі англійською, як у вакансіях
    company_type: str  # одне значення зі списку profile.COMPANY_TYPES
    fit_score: int  # 0..100: реалістичний шанс отримати офер найближчим часом
    why_fits: List[str]  # докази з CV
    gaps: List[str]
    first_steps: List[str]
    search_keywords: List[str]


class CareerMatch(BaseModel):
    candidate_summary: str
    strongest_assets: List[str]
    directions: List[Direction]
    general_advice: str
