"""Дані з онбордингу: хто юзер і куди він хоче."""

from __future__ import annotations

from dataclasses import dataclass

# Напрями навчання, під які є окремі рубрики. Ключ = ім'я файлу в cvmax/rubrics/.
PROGRAMS: dict[str, str] = {
    "economics_big_data": "Economics & data science",
    "business_economics": "Business & economics",
    "software_engineering": "Software engineering",
    "artificial_intelligence": "AI & machine learning",
    "psychology": "Psychology",
    "law": "Law",
    "other": "Other",
}

STATUSES = ["1st year", "2nd year", "3rd year", "4th year", "Master's", "Graduate"]

LEVELS = ["Internship", "Junior / first job", "Mid-level"]

COMPANY_TYPES = [
    "Any / not sure",
    "Consulting (Big 4, MBB, boutique)",
    "Fintech / bank",
    "Big Tech / product company",
    "IT services / outsourcing",
    "Startup",
    "FMCG / retail / industry",
    "Investment fund / finance",
    "Government / international org / NGO",
    "Law firm",
    "Research lab / academia",
    "Other",
]
ANY_COMPANY = COMPANY_TYPES[0]

REGIONS = [
    "US / Canada",
    "UK",
    "EU",
    "Ukraine / Eastern Europe",
    "Remote, anywhere",
]

FEEDBACK_LANGUAGES = {"English": "English", "Українська": "Ukrainian"}


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
    feedback_language: str  # "English" або "Ukrainian"

    def target_clarity(self) -> tuple[str, str]:
        """Наскільки чітко описана ціль. Від цього прямо залежить якість порад."""
        if len(self.vacancy_text.strip()) >= 300:
            return "high", "You added a job posting, so the advice will match its requirements."
        specific_company = self.company_type not in (ANY_COMPANY, "Other") or self.company_details.strip()
        if self.target_role.strip() and specific_company:
            return "medium", "Role and company type are set. Pasting a real job posting makes the advice sharper."
        return "low", (
            "The same job title means different things at different companies: a business analyst in consulting "
            "and one in fintech need different CVs. Add a company type or paste the job posting."
        )

    def to_prompt(self) -> str:
        program = PROGRAMS.get(self.program, self.program)
        vacancy = self.vacancy_text.strip() or "(not provided)"
        return (
            "<candidate_profile>\n"
            f"Field of study: {program}\n"
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
