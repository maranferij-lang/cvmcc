"""Дані з онбордингу: хто юзер і куди він хоче."""

from __future__ import annotations

from dataclasses import dataclass

# Програми КШЕ, під які є окремі рубрики. Ключ = ім'я файлу в cvmax/rubrics/.
PROGRAMS: dict[str, str] = {
    "economics_big_data": "Економіка та великі дані",
    "business_economics": "Бізнес-економіка",
    "software_engineering": "Програмна інженерія",
    "artificial_intelligence": "Штучний інтелект",
    "psychology": "Психологія",
    "law": "Право",
    "other": "Інша програма",
}

STATUSES = ["1 курс", "2 курс", "3 курс", "4 курс", "Магістратура", "Випускник"]

LEVELS = ["Стажування", "Junior / перша робота", "Middle"]

COMPANY_TYPES = [
    "Не знаю / будь-яка",
    "Консалтинг (Big 4, MBB, локальний)",
    "Фінтех / банк",
    "Big Tech / продуктова IT-компанія",
    "IT-аутсорс / аутстаф",
    "Стартап",
    "FMCG / ритейл / індустрія",
    "Інвестиційний фонд / фінанси",
    "Держсектор / міжнародна організація / NGO",
    "Юридична фірма",
    "Дослідницька лабораторія / академія",
    "Інше",
]

REGIONS = [
    "Міжнародні компанії в Україні",
    "Європа",
    "США / Канада",
    "Віддалено, будь-де",
]

FEEDBACK_LANGUAGES = {"Українська": "Ukrainian", "English": "English"}


@dataclass
class Profile:
    program: str  # ключ із PROGRAMS
    status: str
    background: str  # де вчиться / працює / працювала, вільним текстом
    target_role: str
    company_type: str
    company_details: str  # конкретна компанія, індустрія, продукт
    level: str
    region: str
    vacancy_text: str
    feedback_language: str  # "Ukrainian" або "English"

    def target_clarity(self) -> tuple[str, str]:
        """Наскільки чітко описана ціль. Від цього прямо залежить якість порад."""
        if len(self.vacancy_text.strip()) >= 300:
            return "висока", "Є текст вакансії, поради будуть під конкретні вимоги."
        specific_company = self.company_type not in ("Не знаю / будь-яка", "Інше") or self.company_details.strip()
        if self.target_role.strip() and specific_company:
            return "середня", "Роль і тип компанії є. Текст реальної вакансії зробить поради точнішими."
        return "низька", (
            "Одна назва ролі означає різне в різних компаніях. Бізнес-аналітик у консалтингу "
            "і у фінтеху мають різні вимоги. Додай тип компанії або встав текст вакансії."
        )

    def to_prompt(self) -> str:
        program = PROGRAMS.get(self.program, self.program)
        vacancy = self.vacancy_text.strip() or "(not provided)"
        return (
            "<candidate_profile>\n"
            f"University program: {program} (Kyiv School of Economics)\n"
            f"Study status: {self.status}\n"
            f"Background in their own words: {self.background.strip() or '(not provided)'}\n"
            "</candidate_profile>\n"
            "<target>\n"
            f"Target role: {self.target_role.strip() or '(not provided)'}\n"
            f"Company type: {self.company_type}\n"
            f"Company / industry details: {self.company_details.strip() or '(not provided)'}\n"
            f"Level: {self.level}\n"
            f"Region / market: {self.region}\n"
            f"<vacancy_text>\n{vacancy}\n</vacancy_text>\n"
            "</target>"
        )
