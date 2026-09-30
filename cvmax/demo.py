"""Демо-режим без API: фейковий клієнт із заготовленими відповідями.

Потрібен для тестів і щоб подивитись інтерфейс без ключа (CVMAX_DEMO=1).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from .schemas import Analysis, CriterionScore, Edit, Gap, GrillResult, GrillTurn

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
                out = GrillTurn(done=False, question=q, why_asking=why)
            else:
                out = GrillTurn(done=True, question="", why_asking="")
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
