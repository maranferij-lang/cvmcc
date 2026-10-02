"""Застосування прийнятих правок «було / стало» до тексту CV і експорт."""

from __future__ import annotations

import difflib
import io
import re
from dataclasses import dataclass, field

from docx import Document

from .schemas import Edit


@dataclass
class ApplyReport:
    text: str
    applied: list[Edit] = field(default_factory=list)
    added: list[Edit] = field(default_factory=list)
    not_found: list[Edit] = field(default_factory=list)


def _loose_pattern(snippet: str) -> re.Pattern[str]:
    # Текст із PDF часто має зайві переноси й пробіли, тому шукаємо з гнучкими пропусками.
    words = snippet.split()
    return re.compile(r"\s+".join(re.escape(w) for w in words))


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _fuzzy_span(text: str, snippet: str, threshold: float = 0.9) -> tuple[int, int] | None:
    """Шукає фрагмент, майже однаковий зі snippet (модель інколи цитує з дрібною помилкою: Build/Built).

    Порівнюємо snippet з вікнами з 1-6 сусідніх рядків і беремо найсхожіше, якщо схожість не менше threshold.
    """
    target = _norm(snippet)
    if len(target) < 20:
        return None
    lines = text.split("\n")
    offsets, pos = [], 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1
    best: tuple[float, int, int] | None = None
    for i in range(len(lines)):
        for j in range(i, min(i + 6, len(lines))):
            window = "\n".join(lines[i : j + 1])
            ratio = difflib.SequenceMatcher(None, _norm(window), target).ratio()
            if best is None or ratio > best[0]:
                start = offsets[i] + (len(lines[i]) - len(lines[i].lstrip(" •-\t")))
                best = (ratio, start, offsets[j] + len(lines[j]))
    if best and best[0] >= threshold:
        return best[1], best[2]
    return None


def apply_edits(cv_text: str, edits: list[Edit]) -> ApplyReport:
    report = ApplyReport(text=cv_text)
    for edit in edits:
        before = edit.before.strip()
        if not before:
            if edit.after.strip():
                report.added.append(edit)
            continue
        if before in report.text:
            report.text = report.text.replace(before, edit.after.strip(), 1)
            report.applied.append(edit)
            continue
        match = _loose_pattern(before).search(report.text)
        if match:
            report.text = report.text[: match.start()] + edit.after.strip() + report.text[match.end() :]
            report.applied.append(edit)
            continue
        span = _fuzzy_span(report.text, before)
        if span:
            start, end = span
            report.text = report.text[:start] + edit.after.strip() + report.text[end:]
            report.applied.append(edit)
        else:
            report.not_found.append(edit)

    # Прибрані пункти лишають порожні рядки, чистимо їх.
    report.text = re.sub(r"\n{3,}", "\n\n", report.text).strip()

    if report.added:
        by_section: dict[str, list[str]] = {}
        for e in report.added:
            by_section.setdefault(e.section.strip() or "Other", []).append(e.after.strip())
        extra = ["", "", "=== NEW ITEMS (move them into the right section) ==="]
        for section, items in by_section.items():
            extra.append(f"\n{section}")
            extra.extend(f"- {item}" for item in items)
        report.text += "\n".join(extra)
    return report


def changes_markdown(edits: list[Edit]) -> str:
    lines = ["# CVmax: прийняті правки", ""]
    for i, e in enumerate(edits, 1):
        lines.append(f"## {i}. {e.section}")
        if e.before.strip():
            lines.append(f"**Було:** {e.before.strip()}")
        lines.append(f"**Стало:** {e.after.strip() or '(прибрати)'}")
        lines.append(f"_Чому:_ {e.reason.strip()}")
        lines.append("")
    return "\n".join(lines)


def text_to_docx(text: str) -> bytes:
    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------------- Перевірка вигаданих фактів ----------------

# Інструменти й технології, які модель найчастіше «дописує» від себе.
TECH_TERMS = {
    "python", "pandas", "numpy", "scipy", "matplotlib", "seaborn", "plotly", "scikit-learn", "sklearn",
    "pytorch", "tensorflow", "keras", "xgboost", "statsmodels", "beautifulsoup", "selenium", "scrapy",
    "sql", "postgresql", "mysql", "sqlite", "bigquery", "snowflake", "mongodb", "spark", "hadoop", "airflow",
    "excel", "vba", "vlookup", "xlookup", "power query", "powerquery", "pivot", "tableau", "power bi", "looker",
    "r", "stata", "spss", "eviews", "matlab", "sas", "jasp",
    "git", "github", "docker", "kubernetes", "aws", "azure", "gcp", "linux", "jira", "confluence", "notion",
    "figma", "miro", "canva", "hubspot", "salesforce", "sap", "1c", "crm", "a/b", "etl", "api",
    "javascript", "typescript", "react", "node", "java", "kotlin", "swift", "c++", "c#", "go", "django",
    "flask", "fastapi", "langchain", "llm", "gpt",
    # маркетинг і продуктова аналітика
    "google analytics", "ga4", "meta business suite", "ads manager", "google ads", "semrush", "ahrefs",
    "mailchimp", "hotjar", "amplitude", "mixpanel", "dbt", "seo", "ppc", "cac", "ltv", "roas", "ctr", "cpc", "cpa",
}
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+#./-]*[A-Za-z0-9+#]|[A-Za-z]|\d[\d.,]*")
_PHRASES = sorted((t for t in TECH_TERMS if " " in t), key=len, reverse=True)


def _suspicious(token: str) -> bool:
    low = token.lower()
    if low in TECH_TERMS:
        return True
    if any(ch.isdigit() for ch in token):
        return True
    if re.search(r"\.(com|org|io|dev|net|me|ua)\b|/", low):  # посилання: linkedin.com/in/...
        return True
    if len(token) >= 2 and token.isupper():  # SQL, VLOOKUP, KPI
        return True
    return any(c.isupper() for c in token[1:]) and any(c.islower() for c in token)  # PostgreSQL, NumPy


# Числа, які в CV часто написані словами.
_NUMBER_WORDS = {
    "half": "50", "quarter": "25", "third": "33", "twice": "2", "double": "2", "triple": "3",
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
    "eight": "8", "nine": "9", "ten": "10", "dozen": "12", "hundred": "100", "thousand": "1000",
}


# Українські числівники за основою: «п'ять», «пятьма», «десяти»...
_UA_NUMBER_STEMS = {
    "один": "1", "одн": "1", "два": "2", "дві": "2", "двох": "2", "три": "3", "трьох": "3",
    "чотир": "4", "п'ят": "5", "пят": "5", "шіст": "6", "шест": "6", "сім": "7", "сем": "7",
    "вісім": "8", "восьм": "8", "дев'ят": "9", "девят": "9", "десят": "10", "двадцят": "20",
    "тридцят": "30", "сорок": "40", "сто": "100", "тисяч": "1000", "половин": "50",
}

# Назви платформ, які юзери пишуть кирилицею.
_ALIASES = {
    "tiktok": ("тік ток", "тікток", "тик ток", "тикток"),
    "instagram": ("інстаграм", "инстаграм", "інста"),
    "youtube": ("ютуб", "ютюб"),
    "telegram": ("телеграм", "тг"),
    "linkedin": ("лінкедин", "лінкедін"),
    "claude": ("клод", "клоді", "клода"),
    "github": ("гітхаб", "гитхаб"),
    "ai": ("ші", "штучн", "llm", "gpt", "чатгпт", "chatgpt", "claude", "gemini"),
    "kse": ("кше", "київська школа економіки"),
    "sql": ("скл", "сікуель"),
    "excel": ("ексель", "эксель"),
}


def _numbers(text: str) -> set[str]:
    found = {re.sub(r"\D", "", n) for n in re.findall(r"\d[\d.,]*", text)}
    for word in re.findall(r"[a-z]+", text.lower()):
        if word in _NUMBER_WORDS:
            found.add(_NUMBER_WORDS[word])
    for word in re.findall(r"[а-яіїєґ']+", text.lower()):
        for stem, value in _UA_NUMBER_STEMS.items():
            if word.startswith(stem) and len(word) <= len(stem) + 4:
                found.add(value)
                break
    return found


def _known_alias(token: str, known_low: str) -> bool:
    # Синонім має стояти на початку слова: «ші» в «ШІ-школа» рахується, а в «інші» ні.
    return any(re.search(r"(?<![a-zа-яіїєґ])" + re.escape(alias), known_low)
               for alias in _ALIASES.get(token.lower(), ()))


def unverified_terms(after: str, known_text: str) -> list[str]:
    """Назви інструментів і числа з правки, яких немає ні в CV, ні у відповідях юзера.

    Це евристика: вона не ловить усе, але підсвічує найчастіші вигадки моделі.
    """
    visible = re.sub(r"\[[^\]]*\]", " ", after)  # плейсхолдери в дужках не рахуються
    known_low = known_text.lower()
    known_nums = _numbers(known_text)
    flagged: list[str] = []

    for phrase in _PHRASES:
        if phrase in visible.lower() and phrase not in known_low:
            flagged.append(phrase.title())
        visible = re.sub(re.escape(phrase), " ", visible, flags=re.I)

    for token in _TOKEN.findall(visible):
        token = token.strip(".,/-")
        if not token or not _suspicious(token):
            continue
        if token[0].isdigit():
            digits = re.sub(r"\D", "", token)
            if not digits or digits in known_nums or digits.rstrip("0") in known_nums:
                continue
        elif re.search(r"(?<![a-z0-9])" + re.escape(token.lower()) + r"(?![a-z0-9])", known_low):
            continue
        elif _known_alias(token, known_low):
            continue
        if token not in flagged:
            flagged.append(token)
    return flagged


_FACT_NUMBER = re.compile(r"\d[\d.,]*\s?%?")


def lost_facts(before: str, after: str) -> list[str]:
    """Числа з початкового пункту, які зникли в переписаному. Порожній after (видалення) не перевіряємо.

    Сильний пункт тримається на цифрах, і модель іноді губить їх, коли переписує. Юзер має це бачити.
    """
    if not before.strip() or not after.strip():
        return []
    after_digits = {re.sub(r"\D", "", n) for n in _FACT_NUMBER.findall(after)}
    lost = []
    for raw in _FACT_NUMBER.findall(before):
        number = raw.strip().rstrip(".,")
        if re.sub(r"\D", "", number) not in after_digits and number not in lost:
            lost.append(number)
    return lost

