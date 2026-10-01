"""Демо-режим без API: фейковий клієнт із заготовленими відповідями.

Потрібен для тестів і щоб подивитись інтерфейс без ключа (CVMAX_DEMO=1).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from .schemas import Analysis, BuiltCV, LineVerdict, CVEducation, CVEntry, CVSkillGroup, CareerMatch, CriterionScore, Direction, Edit, Gap, GrillResult, GrillTurn

DEMO_QUESTIONS = [
    ("In your Student Council role, how many events did you organise and how many people came?",
     "Числа роблять пункт про лідерство переконливим."),
    ("In the sales dashboard project, what data did you use and what decision did it help make?",
     "Проєкт зараз без результату, а рекрутер шукає саме результат."),
    ("Have you taken part in any case competitions or hackathons? What place?",
     "Для аналітичних ролей це сильний сигнал, якого в CV немає."),
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
            asked = sum(1 for c in self.owner.calls if c["output_format"] is GrillTurn) - 1
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
                        "managing a UAH 40k sponsorship budget",
                        reason="Відповідь у Grill me дала числа, яких не було в CV.",
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
        summary="CV має хорошу базу, але пункти описують обов'язки, а не результати. "
        "Для ролі аналітика у фінтеху не видно SQL, хоча він є у вимогах.",
        target_assumptions=[
            "Роль: Junior Data Analyst у фінтех-компанії, стажування.",
            "Ключові вимоги: SQL, Python або Excel, продуктові метрики, англійська B2+.",
        ],
        scores=[
            CriterionScore(criterion="Target fit", score=3, comment="Найрелевантніший проєкт внизу сторінки."),
            CriterionScore(criterion="Impact bullets", score=2, comment="Більшість пунктів починаються з 'Responsible for'."),
            CriterionScore(criterion="Evidence and numbers", score=2, comment="Жодного числа в досвіді."),
            CriterionScore(criterion="Structure and scannability", score=4, comment="Чиста структура."),
            CriterionScore(criterion="Length and density", score=4, comment="Одна сторінка, добре."),
            CriterionScore(criterion="ATS-friendliness", score=3, comment="Навички в двох колонках, може погано читатись."),
            CriterionScore(criterion="Language quality", score=4, comment="Кілька змін часу в одному пункті."),
        ],
        strengths=["Сильна освіта з релевантними курсами.", "Є проєкт на реальних даних."],
        line_review=[
            LineVerdict(line="Responsible for making reports in Excel", verdict="rewrite",
                        reason="Обов'язок замість результату."),
            LineVerdict(line="Helped the team with client database", verdict="keep",
                        reason="Можна лишити, але це слабкий пункт."),
            LineVerdict(line="Date of birth: 01.01.2004", verdict="cut",
                        reason="Міжнародним компаніям дата народження не потрібна."),
        ],
        edits=[
            Edit(
                section="Experience",
                before="Responsible for making reports in Excel",
                after="Built [N] weekly Excel reports on sales performance for the regional team, "
                "cutting preparation time by [X]%",
                reason="Показує результат замість обов'язку. Встав реальні числа замість дужок.",
                priority="high",
            ),
            Edit(
                section="Skills",
                before="",
                after="SQL (joins, GROUP BY, window functions)",
                reason="SQL є у вимогах вакансії. Додавай, тільки коли реально володієш на базовому рівні.",
                priority="high",
            ),
            Edit(
                section="Personal",
                before="Date of birth: 01.01.2004",
                after="",
                reason="Для міжнародних компаній дата народження в CV не потрібна.",
                priority="medium",
            ),
        ],
        gaps=[
            Gap(
                item="SQL: joins, GROUP BY, window functions",
                why_it_matters="Є в 9 з 10 вакансій аналітика у фінтеху.",
                how_to_close="Безплатний курс на Mode або SQLBolt, потім один проєкт на публічному датасеті з GitHub.",
                time_estimate="3-4 тижні по 5 годин",
                impact="high",
            ),
            Gap(
                item="IELTS Academic 7.0+",
                why_it_matters="Підтверджує рівень англійської для міжнародних компаній.",
                how_to_close="Підготовка за Cambridge IELTS 17-19, потім іспит у British Council.",
                time_estimate="1-2 місяці",
                impact="medium",
            ),
        ],
        missing_info=["Результати проєкту з дашбордом", "Масштаб роботи в студраді"],
    )


DEMO_CV_TEXT = """Olena Petrenko
Kyiv, Ukraine | olena@example.com | linkedin.com/in/olena

EDUCATION
Kyiv School of Economics, BA Economics and Big Data, expected 2027

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
        candidate_summary="Студентка економіки з досвідом у продажах і звітності в Excel, "
        "з першим проєктом на даних і лідерським досвідом у студраді.",
        strongest_assets=["Стажування в продажах", "Excel-звітність", "Економічна освіта КШЕ"],
        directions=[
            Direction(
                role="Sales Operations Intern",
                company_type="FMCG / ритейл / індустрія",
                fit_score=72,
                why_fits=["Вже є стажування в продажах.", "Робила звіти в Excel для команди."],
                gaps=["Немає чисел про результати звітів."],
                first_steps=["Додати в CV результати звітів.", "Податись у 10 компаній FMCG через LinkedIn."],
                search_keywords=["Sales Operations Intern", "Commercial Analyst Intern", "Sales Support"],
            ),
            Direction(
                role="Junior Data Analyst",
                company_type="Фінтех / банк",
                fit_score=48,
                why_fits=["Економіка та великі дані дає базу зі статистики."],
                gaps=["Немає SQL і проєктів на ньому."],
                first_steps=["Пройти курс SQL.", "Зробити один проєкт на публічному датасеті."],
                search_keywords=["Junior Data Analyst", "Data Analyst Intern", "BI Analyst Intern"],
            ),
            Direction(
                role="Business Analyst Intern",
                company_type="Консалтинг (Big 4, MBB, локальний)",
                fit_score=41,
                why_fits=["Лідерство в студраді і комунікація з командою."],
                gaps=["Немає кейс-чемпіонатів."],
                first_steps=["Взяти участь у кейс-чемпіонаті."],
                search_keywords=["Business Analyst Intern", "Consulting Intern"],
            ),
        ],
        general_advice="Найшвидший шлях зараз через продажі й операції, де досвід уже є. "
        "Паралельно почни SQL, щоб за 2-3 місяці відкрити напрям аналітики.",
    )


def demo_built_cv() -> BuiltCV:
    return BuiltCV(
        full_name="Olena Petrenko",
        contact_line=["Kyiv, Ukraine", "olena@example.com", "linkedin.com/in/olena"],
        summary="Economics and Big Data student with sales internship experience, seeking a data analyst internship.",
        education=[CVEducation(institution="Kyiv School of Economics", degree="BA in Economics and Big Data",
                               location="Kyiv, Ukraine", dates="Sep 2023 - Jun 2027 (expected)",
                               details=["Relevant coursework: Econometrics, Statistics, Databases"])],
        experience=[CVEntry(title="Sales Intern", organization="Company X", location="Kyiv", dates="Jun 2025 - Aug 2025",
                            bullets=["Built [N] weekly Excel sales reports for a regional team of [X] people"])],
        projects=[],
        activities=[CVEntry(title="Member", organization="KSE Student Council", location="", dates="2024 - Present",
                            bullets=["Organised 3 events for 150+ students, including a case championship"])],
        skills=[CVSkillGroup(category="Tools", items=["Excel", "Python"])],
        languages=["Ukrainian: Native", "English: B2"],
        awards=[],
        notes_for_user=["Заміни [N] і [X] на реальні числа.", "Додай один проєкт на даних: це найбільше підсилить CV."],
    )
