"""Deterministic CV checks: facts computed without an LLM that go into the prompt and the score.

The model is bad at counting words, bullets and numbers, and does not notice that a CV has no email. Code counts
these easily, so the code gives the model a firm list of facts and the model decides what to do with them.

All regular expressions are linear (no nested quantifiers), each CV line is cut to 400 characters, the whole text
to 40,000 and the number of lines to 1500. So even a CV made of one giant line is parsed instantly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import config
from .cv_input import CVFile
from .profile import Profile

# ---------------- Limits ----------------

MAX_CV_CHARS = 40_000  # the same as cv_input lets through
MAX_LINE_CHARS = 400  # each CV line is cut to this before checking
MAX_LINES = 1500  # non-empty lines; a 6000-word CV does not have that many
MAX_BULLET_CHARS = 1200  # a bullet together with its continuations wrapped onto the following lines
QUOTE_CHARS = 120  # how much CV text a finding may contain
PROMPT_MAX_CHARS = 1800
PROMPT_MAX_FINDINGS = 12
DEFAULT_PENALTY_CAP = 15  # fallback value while config.PENALTY_CAP is not set

SCANNED_WORDS = 30  # a PDF with fewer words is considered a scan
JUNIOR_MAX_WORDS = 650  # Internship / Junior levels: one page
MID_MAX_WORDS = 1300  # Mid-level: up to two pages
MID_MAX_PAGES = 2

# Values from cvmax/profile.py (REGIONS, LEVELS); a test makes sure they do not drift away from those lists.
STRICT_REGIONS = frozenset({"us / canada", "uk"})
MID_LEVEL = "mid-level"

# How many findings of each code we keep, so that one defect does not drown out the rest.
CAPS = {"weak_opener": 8, "duplicate_line": 5, "first_person": 5, "long_bullet": 5, "personal_data": 4}

_WEIGHT = {"high": 4, "medium": 2, "low": 1}
_RANK = {"high": 0, "medium": 1, "low": 2}

_INTRO = (
    "Facts found by automatic checks of the extracted text. "
    "They are deterministic: trust them over your impression of the layout."
)


# ---------------- Types ----------------

@dataclass(frozen=True)
class Finding:
    code: str
    severity: str  # "high" | "medium" | "low"
    message: str  # in English, with no CV text longer than 120 characters
    line: str = ""  # evidence: the CV line (up to 120 characters) or "" for global findings


@dataclass
class CheckReport:
    findings: list[Finding] = field(default_factory=list)
    # words, pages, images, bullets, bullets_with_numbers, sections (a list), has_email, has_phone, has_linkedin
    metrics: dict[str, Any] = field(default_factory=dict)


# ---------------- Line parsing ----------------

_BULLET_CHARS = "-•·*–—●▪■◦○➢➤"
_DECOR = " \t#*_=-–—:|•·~"

# A section is detected by a heading with these words; the Ukrainian words are needed so that a CV in Ukrainian
# does not get a false "no Education".
_KIND_PATTERNS = [
    ("education", r"education|освіта|academic\s+background|qualifications"),
    ("experience", r"experience|досвід"),
    ("projects", r"projects|проєкти|проекти"),
    ("skills", r"skills|навички|вміння"),
    ("languages", r"languages|мови"),
    ("interests", r"interests|інтереси|хобі"),
    ("activities", r"activities|діяльність|активності"),
    ("leadership", r"leadership|лідерство"),
    ("summary", r"summary|профіль|про себе"),
    ("objective", r"objective|мета"),
]
_KIND_RES = [(k, re.compile(r"\b(?:%s)\b" % p, re.I)) for k, p in _KIND_PATTERNS]

# Sections the checks do not analyse but which must close the previous one: otherwise "CERTIFICATES" after SKILLS
# is read as skills, and a dated certificate after EDUCATION breaks the chronology. A heading must consist
# only of these words, so the line "Google Data Analytics Certificate" is not considered a heading.
_OTHER_WORDS = frozenset(
    "certification certifications certificate certificates course courses training trainings award awards "
    "honor honors honour honours achievement achievements volunteering publication publications conference "
    "conferences membership memberships additional information references other miscellaneous misc "
    "сертифікати курси тренінги нагороди досягнення волонтерство публікації конференції додатково інше "
    "додаткова інформація".split()
)
_OTHER_FILLER = frozenset({"and", "&", "key", "selected", "professional", "online", "та", "і", "й"})
_WORD = re.compile(r"[^\W\d_]+|&")

_DIGIT = re.compile(r"\d")
_RANGE = re.compile(
    r"\b((?:19|20)\d\d)\b"  # start year
    r"[^\d\n]{0,14}?(?:[-–—]|\bto\b)\s{0,3}"
    r"(?:\d{1,2}[./]\s{0,2})?(?:[A-Za-z]{3,9}\.?\s{1,3})?"
    r"((?:19|20)\d\d\b|present\b|current\b|now\b|ongoing\b|today\b)",  # end of the range
    re.I,
)
_OPEN_END = 9999  # "present" etc.: the entry is still ongoing
_RANGE_AT_START = re.compile(r"(?:\d{1,2}[./]\s{0,2})?(?:19|20)\d\d\s{0,3}[-–—]")


@dataclass
class _Line:
    raw: str  # the line without surrounding whitespace, up to 400 characters
    text: str  # for a bullet: without the marker and with continuations; otherwise the same as raw
    kind: str  # "bullet" | "heading" | "text"
    kinds: tuple[str, ...]  # kinds of the current section (empty before the first heading)
    section: int  # section number; 0 = before the first heading


def _quote(s: str) -> str:
    return s[:QUOTE_CHARS].rstrip()


def _bullet_text(s: str) -> str | None:
    """Bullet text without the marker; None if the line is not a bullet; "" for separator lines ("-----")."""
    if not s or s[0] not in _BULLET_CHARS:
        return None
    if s[0] == "*" and len(s) > 1 and s[-1] == "*":  # **EDUCATION** is emphasis, not a bullet
        return None
    return s.lstrip(_BULLET_CHARS + " \t").strip()


def _heading_label(label: str) -> str:
    """Line without decoration if it looks like a heading (max 40 chars, no period or comma, max 5 words); else ""."""
    s = label.strip(_DECOR)
    if not s or len(s) > 40 or "." in s or "," in s or len(s.split()) > 5:
        return ""
    return s


def _kinds_of(label: str) -> tuple[str, ...]:
    """Section kinds, if the line looks like a heading."""
    s = _heading_label(label)
    return tuple(k for k, rx in _KIND_RES if rx.search(s)) if s else ()


def _is_other_section(label: str) -> bool:
    """A heading with no kind of its own (Certificates, Awards, Additional...): it only closes the previous section."""
    s = _heading_label(label)
    words = [w.lower() for w in _WORD.findall(s)]
    return any(w in _OTHER_WORDS for w in words) and all(w in _OTHER_WORDS or w in _OTHER_FILLER for w in words)


def _is_continuation(s: str) -> bool:
    """Wrapped bullet line: in a PDF a long bullet breaks over several lines, and the number is often on the second."""
    c = s[0]
    if c.islower() or c in ")%,;&+/":
        return True
    return c.isdigit() and not _RANGE_AT_START.match(s)


def _parse_lines(text: str) -> list[_Line]:
    out: list[_Line] = []
    section = 0
    kinds: tuple[str, ...] = ()
    open_bullet: _Line | None = None  # the last bullet that may still have continuations
    count = 0
    for physical in text.splitlines():
        s = physical.replace(" ", " ").replace("\t", " ").strip()[:MAX_LINE_CHARS].strip()
        if not s:
            open_bullet = None
            continue
        count += 1
        if count > MAX_LINES:
            break
        bullet = _bullet_text(s)
        if bullet is not None:
            if not bullet:
                open_bullet = None
                continue
            open_bullet = _Line(s, bullet, "bullet", kinds, section)
            out.append(open_bullet)
            continue
        if open_bullet is not None and _is_continuation(s):
            if len(open_bullet.text) < MAX_BULLET_CHARS:
                open_bullet.text = (open_bullet.text + " " + s)[:MAX_BULLET_CHARS]
            continue
        open_bullet = None
        # "Skills: Python, SQL": heading and content on one line.
        label, sep, rest = s.partition(":")
        rest = rest.strip()
        label_kinds = _kinds_of(label) if sep and rest else ()
        if label_kinds or (sep and rest and _is_other_section(label)):
            section += 1
            kinds = label_kinds
            out.append(_Line(label.strip(), label.strip(), "heading", kinds, section))
            out.append(_Line(rest, rest, "text", kinds, section))
            continue
        heading_kinds = _kinds_of(s)
        if heading_kinds or _is_other_section(s):
            section += 1
            kinds = heading_kinds
            out.append(_Line(s, s, "heading", kinds, section))
            continue
        out.append(_Line(s, s, "text", kinds, section))
    return out


def _found_sections(lines: list[_Line]) -> list[str]:
    seen: list[str] = []
    for ln in lines:
        if ln.kind == "heading":
            for k in ln.kinds:
                if k not in seen:
                    seen.append(k)
    return seen


# ---------------- Individual checks ----------------

_EMAIL = re.compile(r"[\w.+-]{1,64}@[\w-]{1,63}\.\w[\w.-]{0,62}")
_PHONE_RUN = re.compile(r"[\d\s+()\-–]{9,}")
_DIGITS = re.compile(r"\d+")
_YEAR = re.compile(r"(?:19|20)\d\d")


def _has_phone(line: str) -> bool:
    """9+ digits in a row with +, spaces, parentheses, hyphens; years only ("2023 - 2024 - 2025") are not a phone."""
    for m in _PHONE_RUN.finditer(line):
        groups = _DIGITS.findall(m.group())
        if sum(len(g) for g in groups) >= 9 and not all(_YEAR.fullmatch(g) for g in groups):
            return True
    return False


def _has_email(lines: list[_Line]) -> bool:
    return any("@" in ln.raw and _EMAIL.search(ln.raw) for ln in lines)


# House number after a street name: not a year ("Zara Oxford Street, 2024 – Present") and not the start of a date range
# ("Regent Street 06/2022 – 08/2023"), because that is a workplace, not an address.
_HOUSE_NO = r"(?!(?:19|20)\d\d\b)\d{1,5}\b(?!\s*[-–—]|[./](?:19|20)\d\d)"

_PERSONAL = [
    # date of birth
    re.compile(r"\bdate\s+of\s+birth\b|\bd\.?o\.?b\b|\bbirth\s?date\b|\bbirthday\b|\bborn\s+on\b"
               r"|дата\s+народження|день\s+народження", re.I),
    # age
    re.compile(r"\bage\s*[:\-–]\s*\d{1,2}\b|\bage\s+\d{2}\b|\b\d{2}\s*(?:years?|yrs?)[\s-]*old\b"
               r"|\bвік\s*[:\-–]\s*\d{2}", re.I),
    # marital status
    re.compile(r"\bmarital\b|\bmarried\b|\bfamily\s+status\b|сімейний\s+стан", re.I),
    # nationality and gender only as fields ("Gender: …"), so as not to hit "Gender Equality Research"
    re.compile(r"\bnationality\s*[:\-–]|\bgender\s*[:\-–]|\bsex\s*[:\-–]|національність\s*[:\-–]"
               r"|\bстать\s*[:\-–]", re.I),
    re.compile(r"\bpassport\b|\bпаспорт", re.I),
    # full street address: street and house number. "3 street food festivals" does not match.
    re.compile(r"\b\d{1,5}[A-Za-z]?\s+(?:[A-Z][\w'’.-]*\s+){1,3}(?:Street|Str\.|str\.)"),
    re.compile(r"\b[Ss]tr\.\s*[\w'’.-]+(?:\s+[\w'’.-]+)?[\s,]{1,3}" + _HOUSE_NO),
    re.compile(r"\b[A-Z][\w'’.-]*\s+(?:Street|STREET)[\s,]{1,3}" + _HOUSE_NO),
    re.compile(r"(?<!\w)(?:вул\.|вулиця|ул\.)\s*[\w'’.-]+(?:\s+[\w'’.-]+)?[\s,]{1,3}(?:буд\.?\s*)?\d{1,5}", re.I),
    # "Photo attached", "Photo:", "| Photo |"
    re.compile(r"(?:^|[|;,•·]\s*)photo(?:graph)?\s*(?::|\||;|,|$|attached\b|enclosed\b|below\b)"
               r"|\bphoto\s+attached\b", re.I),
]
_PERSONAL_MAX_LINE = 200  # personal data sits in the header as a short line, not in a long description
# Personal block in bullets ("- Date of birth: 14.03.2005"). We do not apply the full set of patterns to bullets:
# it would hit "- Taught Python to 12 children between 10 and 13 years old" or "- Analysed passport data".
_PERSONAL_FIELD = re.compile(
    r"(?:date\s+of\s+birth|d\.?o\.?b\.?|birth\s?date|birthday|born\s+on|age|marital\s+status|family\s+status"
    r"|nationality|gender|sex|passport|дата\s+народження|день\s+народження|вік|сімейний\s+стан"
    r"|національність|стать|паспорт)\s*(?::|[-–—]\s|\s\d)",
    re.I,
)


def _check_personal_data(lines: list[_Line], profile: Profile) -> list[Finding]:
    region = " ".join((getattr(profile, "region", "") or "").lower().split())
    severity = "high" if region in STRICT_REGIONS else "medium"
    out: list[Finding] = []
    for ln in lines:
        if ln.kind == "heading" or len(ln.raw) > _PERSONAL_MAX_LINE:
            continue
        if ln.kind == "bullet":
            hit = bool(_PERSONAL_FIELD.match(ln.text))
        else:
            hit = any(rx.search(ln.raw) for rx in _PERSONAL)
        if hit:
            out.append(Finding("personal_data", severity,
                               "Personal data that international employers do not need", _quote(ln.raw)))
            if len(out) >= CAPS["personal_data"]:
                break
    return out


def _check_length(profile: Profile, words: int, pages: int | None, scanned: bool) -> list[Finding]:
    mid = MID_LEVEL in (getattr(profile, "level", "") or "").lower()
    size = []
    if pages:
        size.append(f"{pages} page{'s' if pages != 1 else ''}")
    if not scanned:  # a scan has no words, so the word count says nothing
        size.append(f"about {words} words")
    if mid:
        over = (pages or 0) > MID_MAX_PAGES or (not scanned and words > MID_MAX_WORDS)
        advice = f"for a mid-level role keep it to {MID_MAX_PAGES} pages (about {MID_MAX_WORDS} words at most)"
    else:
        over = (pages or 0) >= 2 or (not scanned and words > JUNIOR_MAX_WORDS)
        advice = f"an internship or junior CV should fit on one page (about {JUNIOR_MAX_WORDS} words)"
    if not over:
        return []
    return [Finding("length_over", "high", f"The CV is {' and '.join(size)}; {advice}.")]


_WEAK_OPENER = re.compile(
    r"(responsible\s+for|helped|assisted|worked\s+on|participated\s+in|supported|involved\s+in"
    r"|duties\s+included|tasked\s+with)\b",
    re.I,
)


def _check_weak_openers(bullets: list[_Line]) -> list[Finding]:
    out: list[Finding] = []
    for b in bullets:
        m = _WEAK_OPENER.match(b.text)
        if m:
            phrase = " ".join(m.group(1).split()).capitalize()
            out.append(Finding("weak_opener", "medium",
                               f'Bullet opens with "{phrase}", which names a duty instead of an action and a result.',
                               _quote(b.raw)))
            if len(out) >= CAPS["weak_opener"]:
                break
    return out


def _check_few_numbers(bullets: list[_Line]) -> list[Finding]:
    work = [b for b in bullets if {"experience", "projects"} & set(b.kinds)]
    if len(work) < 4:
        return []
    without = sum(1 for b in work if not _DIGIT.search(b.text))
    if (len(work) - without) / len(work) >= 0.30:
        return []
    return [Finding("few_numbers", "medium",
                    f"{without} of {len(work)} bullets under Experience and Projects have no number.")]


def _check_duplicates(lines: list[_Line]) -> list[Finding]:
    # Imported here, not at the top: analyze.py itself imports this module, and a top-level import would create a cycle.
    from .analyze import _skeleton

    seen: set[str] = set()
    out: list[Finding] = []
    for ln in lines:
        if ln.kind == "heading" or "@" in ln.raw or "linkedin.com/" in ln.raw.lower():
            continue
        sk = _skeleton(ln.raw)
        # Pure dates and numbers ("2024 2025 2026") do not count as a repeat.
        if len(sk) < 25 or sum(1 for w in sk.split() if not w.isdigit()) < 4:
            continue
        if sk in seen:
            out.append(Finding("duplicate_line", "medium",
                               "This line repeats an earlier line of the CV; say it once.", _quote(ln.raw)))
            if len(out) >= CAPS["duplicate_line"]:
                break
        else:
            seen.add(sk)
    return out


_FIRST_PERSON_START = re.compile(r"(?:I|My|Me)\s|I['’](?:m|ve|d|ll)\b")
_PUNCT = ",.;:!?()\"'"
_CLAUSE_OPENERS = frozenset({"when", "while", "as", "after", "before", "since", "because", "if", "once", "where",
                             "whenever", "although", "though", "then", "so", "but", "also", "that", "which"})


def _has_mid_sentence_i(words: list[str]) -> bool:
    """The pronoun "I" among words 2–6. A Roman numeral in a course name ("Calculus I, Physics II", "Phase I trial")
    is not a pronoun: it is preceded by a capitalised word and followed by a comma, "and" or the end of the line."""
    for i in range(1, min(len(words), 6)):
        if words[i].strip(_PUNCT) != "I" or i + 1 >= len(words):
            continue
        nxt = words[i + 1]
        if not nxt[0].islower() or nxt.lower() in ("and", "or"):
            continue
        prev = words[i - 1]
        stem = prev.strip(_PUNCT)
        if not stem or stem[0].islower() or prev[-1] in ".!?;:" or stem.lower() in _CLAUSE_OPENERS:
            return True
    return False


def _check_first_person(lines: list[_Line]) -> list[Finding]:
    out: list[Finding] = []
    for i, ln in enumerate(lines):
        if i == 0 or ln.kind == "heading":  # the first line is the name: "My Linh" is not a pronoun
            continue
        text = ln.text
        if _FIRST_PERSON_START.match(text) or _has_mid_sentence_i(text.split()):
            out.append(Finding("first_person", "low",
                               "Written in the first person; start with an action verb instead.", _quote(ln.raw)))
            if len(out) >= CAPS["first_person"]:
                break
    return out


_REFERENCES = re.compile(r"\breferences?\W{0,3}(?:are\s+)?(?:available|upon\s+request|on\s+request)\b", re.I)
_CV_TITLES = {"curriculum vitae", "cv", "resume", "résumé", "резюме"}


def _check_title_and_references(lines: list[_Line]) -> tuple[list[Finding], list[Finding]]:
    refs: list[Finding] = []
    title: list[Finding] = []
    for ln in lines:
        if not refs and _REFERENCES.search(ln.raw):
            refs.append(Finding("references_line", "low",
                                "A references line takes space; employers ask for references when they need them.",
                                _quote(ln.raw)))
        if not title and ln.kind != "bullet" and ln.raw.strip(_DECOR + ".").lower() in _CV_TITLES:
            title.append(Finding("cv_title", "low",
                                 "A separate CV or Resume title line wastes the top of the page; the name goes first.",
                                 _quote(ln.raw)))
    return refs, title


def _check_objective(lines: list[_Line]) -> list[Finding]:
    for ln in lines:
        if ln.kind == "heading" and "objective" in ln.kinds:
            return [Finding("objective_section", "low",
                            "An Objective section is generic; a specific summary or nothing works better.",
                            _quote(ln.raw))]
    return []


def _check_long_bullets(bullets: list[_Line]) -> list[Finding]:
    out: list[Finding] = []
    for b in bullets:
        n = len(b.text.split())
        if n > 40:
            out.append(Finding("long_bullet", "low",
                               f"Bullet is {n} words long; cut it to one or two lines.", _quote(b.raw)))
            if len(out) >= CAPS["long_bullet"]:
                break
    return out


_DATED_KINDS = ("experience", "projects", "education")


def _check_chronology(lines: list[_Line]) -> list[Finding]:
    by_section: dict[int, list[tuple[int, int, str]]] = {}
    section_kind: dict[int, str] = {}
    for ln in lines:
        if ln.kind != "text":
            continue
        kind = next((k for k in _DATED_KINDS if k in ln.kinds), None)
        if kind is None:
            continue
        m = _RANGE.search(ln.raw)
        if m:
            end = m.group(2)
            by_section.setdefault(ln.section, []).append(
                (int(m.group(1)), int(end) if end.isdigit() else _OPEN_END, ln.raw))
            section_kind[ln.section] = kind
    out: list[Finding] = []
    for section, entries in by_section.items():
        if len(entries) < 2:
            continue
        pairs = list(zip(entries, entries[1:]))
        # A rising start alone is not enough: an ongoing entry above a shorter one ("Tutor 2021 – Present", then
        # "Intern 2024") or an exchange semester within a degree are in the right order. An equal end
        # ("Nova Retail 2022 – Present" above roles "2024 – Present") is a company above its roles, not a violation.
        ascending = [a for a, b in pairs if a[0] < b[0] and a[1] < b[1]]
        descending = sum(1 for a, b in pairs if a[0] > b[0])
        if ascending and len(ascending) >= descending:
            name = section_kind[section].capitalize()
            out.append(Finding("not_reverse_chronological", "medium",
                               f"Entries under {name} start with the oldest; put the newest first.",
                               _quote(ascending[0][2])))
    return out


_SPLIT_TERMS = re.compile(r"[,;|•·]")
# Split "SQL/PostgreSQL" if at least one side is a word of 3+ letters; "CI/CD", "A/B", "UI/UX" stay whole.
_SPLIT_SLASH = re.compile(r"(?<=\w\w\w)\s*/\s*|\s*/\s*(?=\w\w\w)")
_PAREN = re.compile(r"\([^)]*\)")
# Parentheses holding only a language level (C1, native): "English (C1)", but not "Excel (A1 notation)".
_LANG_LEVEL = re.compile(r"\(\s*(?:[abc][12]|native(?:\s+speaker)?|mother\s+tongue|bilingual)\s*[,)–—-]", re.I)
# Level and vendor are not part of what we search for in the text: "MS Excel" and "Advanced Python"
# confirm "Excel" and "Python".
_LEVEL_WORDS = frozenset({"ms", "microsoft", "advanced", "basic", "intermediate", "beginner", "proficient", "expert"})
MAX_SKILL_TERMS = 60


def _drop_parens(s: str) -> str:
    """Strips parenthetical qualifiers; "English (C1)" is a language, not a skill, so it leaves a NUL marker instead."""
    return _PAREN.sub(lambda m: "\x00" if _LANG_LEVEL.match(m.group()) else " ", s)


def _evidence_key(term: str) -> str:
    words = term.lower().split()
    while len(words) > 1 and words[0] in _LEVEL_WORDS:
        words.pop(0)
    while len(words) > 1 and words[-1] in _LEVEL_WORDS:
        words.pop()
    return " ".join(words)


def _skill_terms(skill_lines: list[_Line]) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for ln in skill_lines:
        s = ln.text
        label, sep, rest = s.partition(":")
        if sep and rest.strip() and len(label) <= 30 and "," not in label:
            s = rest  # "Tools: Excel, Tableau": the label is not a skill
        for part in _SPLIT_TERMS.split(_drop_parens(s)):
            if "\x00" in part:
                continue
            for piece in _SPLIT_SLASH.split(part):
                term = " ".join(piece.strip(" .:-–—*").split())
                low = term.lower()
                if not 2 <= len(term) <= 30 or low in seen or not any(c.isalnum() for c in term) or term.isdigit():
                    continue
                seen.add(low)
                terms.append(term)
                if len(terms) >= MAX_SKILL_TERMS:
                    return terms
    return terms


def _check_skills_evidence(lines: list[_Line]) -> list[Finding]:
    skill_lines = [ln for ln in lines if ln.kind != "heading" and "skills" in ln.kinds]
    terms = _skill_terms(skill_lines)
    if not terms:
        return []
    skill_ids = {id(ln) for ln in skill_lines}
    rest = "\n".join(ln.text if ln.kind == "bullet" else ln.raw for ln in lines if id(ln) not in skill_ids).lower()
    missing = [t for t in terms
               if not re.search(r"(?<!\w)" + re.escape(_evidence_key(t)) + r"(?!\w)", rest)]
    if not missing:
        return []
    shown: list[str] = []
    for t in missing[:5]:
        if len(", ".join(shown + [t])) > QUOTE_CHARS:
            break
        shown.append(t)
    more = len(missing) - len(shown)
    tail = f" (+{more} more)" if more else ""
    return [Finding("skills_no_evidence", "low",
                    f"Skills listed but never mentioned elsewhere in the CV: {', '.join(shown)}{tail}.")]


# ---------------- Main function ----------------

def run_checks(cv: CVFile, profile: Profile) -> CheckReport:
    text = (cv.text or "")[:MAX_CV_CHARS]
    words = len(text.split())
    pages = cv.pages if isinstance(cv.pages, int) and not isinstance(cv.pages, bool) else None
    images = cv.images if isinstance(getattr(cv, "images", None), int) else None
    is_pdf = cv.pdf_bytes is not None or pages is not None or (cv.filename or "").lower().endswith(".pdf")

    lines = _parse_lines(text)
    bullets = [ln for ln in lines if ln.kind == "bullet"]
    all_text = "\n".join(ln.raw for ln in lines).lower()
    has_email = _has_email(lines)
    has_phone = any(_has_phone(ln.raw) for ln in lines)
    has_linkedin = "linkedin.com/" in all_text
    sections = _found_sections(lines)
    metrics: dict[str, Any] = {
        "words": words,
        "pages": pages,
        "images": images,
        "bullets": len(bullets),
        "bullets_with_numbers": sum(1 for b in bullets if _DIGIT.search(b.text)),
        "sections": sections,
        "has_email": has_email,
        "has_phone": has_phone,
        "has_linkedin": has_linkedin,
    }

    findings: list[Finding] = []
    scanned = is_pdf and words < SCANNED_WORDS
    if scanned:
        findings.append(Finding(
            "scanned_pdf", "high",
            "The PDF has almost no extractable text (it looks scanned or image-only), "
            "so automatic parsers will see an empty CV.",
        ))
    if words or pages:
        findings += _check_length(profile, words, pages, scanned)
    if scanned or not words:
        # The text is empty: the remaining checks would give only false "no email", "no Education" and the like.
        return CheckReport(findings, metrics)

    findings += _check_personal_data(lines, profile)
    if images and images >= 1:  # only PDFs fill images, so there is no need to check the format separately
        findings.append(Finding(
            "photo_or_graphics", "medium",
            f"The PDF has {images} embedded image{'s' if images != 1 else ''} (photo, logo or graphics) "
            "that automatic parsers ignore; many employers do not want a photo.",
        ))
    if not has_email:
        findings.append(Finding("no_email", "medium", "No email address found in the text."))
    if not has_phone:
        findings.append(Finding("no_phone", "low", "No phone number found in the text."))
    if not has_linkedin:
        findings.append(Finding("no_linkedin", "low", "No LinkedIn link (linkedin.com/...) found in the text."))
    findings += _check_weak_openers(bullets)
    findings += _check_few_numbers(bullets)
    findings += _check_duplicates(lines)
    findings += _check_first_person(lines)
    refs, title = _check_title_and_references(lines)
    findings += refs + title
    findings += _check_objective(lines)
    findings += _check_long_bullets(bullets)
    findings += _check_chronology(lines)
    if "education" not in sections:
        findings.append(Finding("missing_education", "medium", "No Education section heading found."))
    findings += _check_skills_evidence(lines)
    return CheckReport(findings, metrics)


# ---------------- Score and prompt ----------------

def penalty(report: CheckReport) -> int:
    """Score penalty: high=4, medium=2, low=1, the sum is capped at config.PENALTY_CAP."""
    total = sum(_WEIGHT.get(f.severity, 0) for f in report.findings)
    cap = getattr(config, "PENALTY_CAP", DEFAULT_PENALTY_CAP)
    return min(total, cap)


def _count(metrics: dict[str, Any], key: str) -> int | None:
    v = metrics.get(key)
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _metrics_line(metrics: dict[str, Any]) -> str:
    parts = []
    for key, noun in (("words", "word"), ("pages", "page"), ("bullets", "bullet")):
        n = _count(metrics, key)
        if n is not None:
            parts.append(_plural(n, noun))
    n = _count(metrics, "bullets_with_numbers")
    if n is not None:
        parts.append(f"{n} with numbers")
    n = _count(metrics, "images")
    if n is not None:
        parts.append(_plural(n, "embedded image"))
    return "Metrics: " + ", ".join(parts) + "." if parts else "Metrics: none."


_TAG_LIKE = re.compile(r"<(/?)checks", re.I)
_ROW_MAX_CHARS = 330


def _row(f: Finding) -> str:
    # The CV text goes into the prompt as is (nothing is escaped), but it cannot close the <checks> block from inside:
    # neither a CV line nor a message can (skills_no_evidence lists terms taken from the CV).
    message = _TAG_LIKE.sub(r"< \1checks", " ".join(f.message.split()))
    row = f"- [{f.severity}] {message}"
    if f.line:
        quoted = _TAG_LIKE.sub(r"< \1checks", " ".join(f.line[:QUOTE_CHARS].split()))
        row = f'- [{f.severity}] {message.rstrip(". ")}: "{quoted}"'
    return row[:_ROW_MAX_CHARS]


def render_for_prompt(report: CheckReport) -> str:
    """The <checks> block for the model request: up to 1800 characters and up to 12 findings, high first."""
    ordered = sorted(report.findings, key=lambda f: _RANK.get(f.severity, len(_RANK)))
    tail = _metrics_line(report.metrics) + "\n</checks>"
    head = f"<checks>\n{_INTRO}"
    # Room for the "+N more" line so that it always fits.
    budget = PROMPT_MAX_CHARS - len(head) - len(tail) - 2 - len("+99999999 more\n")
    rows: list[str] = []
    used = 0
    for f in ordered[:PROMPT_MAX_FINDINGS]:
        row = _row(f)
        if used + len(row) + 1 > budget:
            break
        rows.append(row)
        used += len(row) + 1
    body = rows
    if not ordered:
        body = ["No problems found by the automatic checks."]
    elif len(ordered) > len(rows):
        body = rows + [f"+{len(ordered) - len(rows)} more"]
    return "\n".join([head, *body, tail])
