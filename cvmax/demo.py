"""Демо-режим без API: фейковий клієнт із заготовленими відповідями.

Потрібен для тестів і щоб подивитись інтерфейс без ключа (CVMAX_DEMO=1).
"""

from __future__ import annotations

import re

from types import SimpleNamespace
from typing import Any

from .schemas import Analysis, BuiltCV, LineVerdict, CVEducation, CVEntry, CVSkillGroup, CareerMatch, CriterionScore, Direction, Edit, Gap, GrillResult, GrillTurn

DEMO_QUESTIONS = [
    ("In your Student Council role, how many events did you organise and how many people came?",
     "Numbers make a leadership line convincing."),
    ("In the sales dashboard project, what data did you use and what decision did it help make?",
     "The project has no result yet, and results are what recruiters look for."),
    ("Have you taken part in any case competitions or hackathons? What place?",
     "For analyst roles this is a strong signal that your CV is missing."),
]


class _Messages:
    def __init__(self, owner: "FakeClient") -> None:
        self.owner = owner

    def parse(self, **kwargs: Any) -> SimpleNamespace:
        self.owner.calls.append(kwargs)
        fmt = kwargs["output_format"]
        if fmt is Analysis:
            out: Any = demo_analysis()
        elif fmt is GrillTurn:
            # Номер питання беремо з інструкції цієї сесії, а не з усіх викликів процесу.
            found = re.search(r"Questions asked so far: (\d+)", str(kwargs.get("messages", "")))
            asked = int(found.group(1)) if found else 0
            if asked < len(DEMO_QUESTIONS):
                q, why = DEMO_QUESTIONS[asked]
                out = GrillTurn(done=False, kind="deepen", question=q, why_asking=why)
            else:
                out = GrillTurn(done=True, kind="deepen", question="", why_asking="")
        elif fmt is GrillResult:
            out = GrillResult(
                new_facts=["Organised 6 events for 300+ students as Student Council member."],
                edits=[
                    Edit(
                        section="Leadership & Activities",
                        before="Member of Student Council",
                        after="Organised 6 university events for 300+ students as Student Council member, "
                        "managing a $1,000 sponsorship budget",
                        reason="Your Q&A answer added numbers that were not in the CV.",
                        priority="high",
                    )
                ],
            )
        elif fmt is CareerMatch:
            out = demo_career()
        elif fmt is BuiltCV:
            out = demo_built_cv()
        else:
            raise ValueError(f"Unknown output format: {fmt}")
        return SimpleNamespace(stop_reason="end_turn", parsed_output=out)


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.messages = _Messages(self)


def demo_analysis() -> Analysis:
    return Analysis(
        overall_score=58,
        summary="Solid base, but the bullets describe duties instead of results. "
        "For a fintech analyst role, SQL is missing even though the posting asks for it.",
        target_assumptions=[
            "Role: Junior Data Analyst at a fintech company, internship.",
            "Key requirements: SQL, Python or Excel, product metrics.",
        ],
        scores=[
            CriterionScore(criterion="Target fit", score=3, comment="The most relevant project sits at the bottom of the page."),
            CriterionScore(criterion="Impact bullets", score=2, comment="Most bullets start with 'Responsible for'."),
            CriterionScore(criterion="Evidence and numbers", score=2, comment="No numbers in the experience section."),
            CriterionScore(criterion="Structure and scannability", score=4, comment="Clean structure."),
            CriterionScore(criterion="Length and density", score=4, comment="One page, good."),
            CriterionScore(criterion="ATS-friendliness", score=3, comment="Two-column skills block may not parse well."),
            CriterionScore(criterion="Language quality", score=4, comment="Tense changes within one bullet."),
        ],
        strengths=["Strong education with relevant coursework.", "A project on real data."],
        line_review=[
            LineVerdict(line="Responsible for making reports in Excel", verdict="rewrite",
                        reason="A duty, not a result."),
            LineVerdict(line="Helped the team with client database", verdict="keep",
                        reason="Can stay, but it is a weak bullet."),
            LineVerdict(line="Date of birth: 01.01.2004", verdict="cut",
                        reason="International employers do not need your date of birth."),
        ],
        edits=[
            Edit(
                section="Experience",
                before="Responsible for making reports in Excel",
                after="Built [N] weekly Excel reports on sales performance for the regional team, "
                "cutting preparation time by [X]%",
                reason="Shows a result instead of a duty. Replace the brackets with real numbers.",
                priority="high",
            ),
            Edit(
                section="Skills",
                before="",
                after="SQL (joins, GROUP BY, window functions)",
                reason="The posting asks for SQL. Add it only if you really know the basics.",
                priority="high",
            ),
            Edit(
                section="Personal",
                before="Date of birth: 01.01.2004",
                after="",
                reason="International employers do not need your date of birth on a CV.",
                priority="medium",
            ),
        ],
        gaps=[
            Gap(
                item="SQL: joins, GROUP BY, window functions",
                why_it_matters="Listed in 9 of 10 fintech analyst postings.",
                how_to_close="A free course on Mode or SQLBolt, then one project on a public dataset on GitHub.",
                time_estimate="3-4 weeks, 5 hours a week",
                impact="high",
            ),
            Gap(
                item="IELTS Academic 7.0+",
                why_it_matters="Proves your English level to international employers.",
                how_to_close="Prepare with Cambridge IELTS 17-19, then book the exam.",
                time_estimate="1-2 months",
                impact="medium",
            ),
        ],
        missing_info=["Results of the dashboard project", "Scale of the student council work"],
    )


DEMO_CV_TEXT = """Maya Chen
London, UK | maya@example.com | linkedin.com/in/mayachen

EDUCATION
Northbridge University, BSc Economics and Data Science, expected 2027

EXPERIENCE
Sales Intern, Company X, Jun 2025 - Aug 2025
- Responsible for making reports in Excel
- Helped the team with client database

ACTIVITIES
Member of Student Council

PERSONAL
Date of birth: 01.01.2004
"""


def demo_career() -> CareerMatch:
    return CareerMatch(
        candidate_summary="Economics student with sales and Excel reporting experience, "
        "a first data project and student council leadership.",
        strongest_assets=["Sales internship", "Excel reporting", "Economics degree"],
        directions=[
            Direction(
                role="Sales Operations Intern",
                company_type="FMCG / retail / industry",
                fit_score=72,
                why_fits=["Already has a sales internship.", "Built Excel reports for the team."],
                gaps=["No numbers on what the reports achieved."],
                first_steps=["Add report results to the CV.", "Apply to 10 FMCG companies via LinkedIn."],
                search_keywords=["Sales Operations Intern", "Commercial Analyst Intern", "Sales Support"],
            ),
            Direction(
                role="Junior Data Analyst",
                company_type="Fintech / bank",
                fit_score=48,
                why_fits=["Economics and data science gives a statistics base."],
                gaps=["No SQL and no projects using it."],
                first_steps=["Take a SQL course.", "Do one project on a public dataset."],
                search_keywords=["Junior Data Analyst", "Data Analyst Intern", "BI Analyst Intern"],
            ),
            Direction(
                role="Business Analyst Intern",
                company_type="Consulting (Big 4, MBB, boutique)",
                fit_score=41,
                why_fits=["Student council leadership and teamwork."],
                gaps=["No case competitions yet."],
                first_steps=["Enter a case competition."],
                search_keywords=["Business Analyst Intern", "Consulting Intern"],
            ),
        ],
        general_advice="The fastest path right now is sales and operations, where you already have experience. "
        "Start SQL in parallel to open up analyst roles in 2-3 months.",
    )


def demo_built_cv() -> BuiltCV:
    return BuiltCV(
        full_name="Maya Chen",
        contact_line=["London, UK", "maya@example.com", "linkedin.com/in/mayachen"],
        summary="Economics and Data Science student with sales internship experience, seeking a data analyst internship.",
        education=[CVEducation(institution="Northbridge University", degree="BSc in Economics and Data Science",
                               location="London, UK", dates="Sep 2023 - Jun 2027 (expected)",
                               details=["Relevant coursework: Econometrics, Statistics, Databases"])],
        experience=[CVEntry(title="Sales Intern", organization="Company X", location="London", dates="Jun 2025 - Aug 2025",
                            bullets=["Built [N] weekly Excel sales reports for a regional team of [X] people"])],
        projects=[],
        activities=[CVEntry(title="Member", organization="Student Council", location="", dates="2024 - Present",
                            bullets=["Organised 3 events for 150+ students, including a case championship"])],
        skills=[CVSkillGroup(category="Tools", items=["Excel", "Python"])],
        languages=["English: Native", "Spanish: B2"],
        awards=[],
        notes_for_user=["Replace [N] and [X] with real numbers.", "Add one data project: it will strengthen the CV the most."],
    )
