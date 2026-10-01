"""«Куди податись»: за CV пропонує напрями, де в людини найбільше шансів."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import config
from .cv_input import CVFile
from .llm import ask_structured
from .profile import COMPANY_TYPES, PROGRAMS
from .prompts import load_rubric
from .schemas import CareerMatch

DEFAULT_COMPANY_TYPE = COMPANY_TYPES[0]


@dataclass
class CareerPrefs:
    program: str
    status: str
    interests: str  # що подобається, чого хочеться
    avoid: str  # чого точно не хочеться
    level: str
    region: str
    feedback_language: str

    def to_prompt(self) -> str:
        return (
            "<candidate_preferences>\n"
            f"University program: {PROGRAMS.get(self.program, self.program)} (Kyiv School of Economics)\n"
            f"Study status: {self.status}\n"
            f"Interests: {self.interests.strip() or '(not provided)'}\n"
            f"Does not want: {self.avoid.strip() or '(not provided)'}\n"
            f"Level: {self.level}\n"
            f"Region / market: {self.region}\n"
            "</candidate_preferences>"
        )


def career_system(prefs: CareerPrefs) -> str:
    company_types = "\n".join(f"- {c}" for c in COMPANY_TYPES)
    return f"""You are CVmax, a career coach for university students and early-career people.
You know the entry-level job market in Ukraine and for international companies well.

The candidate does not know where to apply. From their CV and preferences, find the directions where
they have the best realistic chance of getting an internship or entry-level offer soon. Be honest:
rank by realistic chance, not by prestige or by what sounds exciting. A direction the CV already
supports well beats a dream role that needs a year of preparation.

Write every explanation in {prefs.feedback_language}. Write role titles and search keywords in English,
exactly as they appear in job postings.

Rules:
- Propose 4 or 5 directions, best chance first. Include at least one adjacent option the candidate
  probably has not considered but their CV supports.
- For each direction, company_type must be copied exactly from this list:
{company_types}
- fit_score: 0-100, the realistic chance that this CV, as it is today plus the first steps, gets an offer
  in this direction within the next few months. Calibrate honestly against real entry-level competition:
  80-100 = the CV already matches typical postings and similar candidates routinely get offers;
  60-79 = a solid candidate, needs a better CV or one small addition;
  40-59 = plausible, but needs 1-2 months of targeted work;
  below 40 = a long shot for now.
  Most student CVs have their best direction in the 50-75 range. Do not give several directions 80+
  unless the CV is genuinely strong. Skills that are only listed, without any project or job using them,
  count for little.
- why_fits: concrete evidence from the CV, not generic praise. Never invent anything the CV does not say.
- gaps: what is missing for this direction. first_steps: 2-3 concrete actions for the next month.
- search_keywords: 3-5 job titles or keywords to type into LinkedIn, Djinni, Work.ua or DOU.
- Respect what the candidate does not want.
- strongest_assets: the 3-5 things in the CV that employers will value most.
- general_advice: 2-3 sentences, the single most useful thing to do first.

Reference rubric of what employers look for:

{load_rubric(prefs.program)}"""


def match_careers(llm: Any, prefs: CareerPrefs, cv: CVFile) -> CareerMatch:
    content = cv.as_content_blocks() + [
        {"type": "text", "text": prefs.to_prompt() + "\n\nWhere should this candidate apply?"}
    ]
    result = ask_structured(
        llm,
        system=career_system(prefs),
        content=content,
        output_model=CareerMatch,
        effort=config.EFFORT_ANALYSIS,
    )
    for d in result.directions:
        d.fit_score = max(0, min(100, d.fit_score))
        if d.company_type not in COMPANY_TYPES:
            d.company_type = DEFAULT_COMPANY_TYPE
    result.directions.sort(key=lambda d: d.fit_score, reverse=True)
    return result
