"""Remove direct contact details from a CV text and its review before an opt-in copy is saved.

What is removed: emails, phone numbers, links and domains, the person's own name (the words of the Google display
name) and the date of birth. Street addresses are not attempted (too error-prone), so this is not a promise that
all personal data is gone; the privacy text says exactly what is removed.

All regexes are linear: bounded repeats, no nested quantifiers. Input is capped at MAX_CHARS.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# One place for the consent text version. Change it when the wording of the consent or the privacy section changes.
CONSENT_VERSION = "2026-10-17"

MAX_CHARS = 40_000
MAX_DEPTH = 12
MAX_NAME_WORDS = 12

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]{1,63}(?:\.[A-Za-z0-9\-]{1,63}){1,6}")
_URL = re.compile(r"(?i:https?://|www\.)[^\s<>\"'\[\]]{1,300}")
_TLDS = "com|org|net|io|dev|app|ai|edu|info|ua|uk|de|eu|fr|pl|nl|us|ca|ru|by|kz|pt|it|es|cz|se|ch|tech|xyz|me"
_DOMAIN = re.compile(
    r"(?<![\w.@-])[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?){0,4}"
    rf"\.(?:{_TLDS})(?![\w])(?:/[^\s<>\"'\[\]]{{0,200}})?",
    re.IGNORECASE,
)
_SERVICES = "viber|whatsapp|instagram|telegram|skype|github|behance|dribbble"
_LABELLED_HANDLE = re.compile(
    rf"(?<![^\W_])(?:{_SERVICES})(?:[ \t]{{0,3}}/[ \t]{{0,3}}(?:{_SERVICES})){{0,7}}[ \t]{{0,3}}[:\uff1a][ \t]{{0,3}}"
    r"@?[A-Za-z0-9_](?:[A-Za-z0-9_.\-]{0,62}[A-Za-z0-9_])?",
    re.IGNORECASE,
)
_BARE_HANDLE = re.compile(r"(?<!\w)@[A-Za-z0-9_.]{3,32}")
_TECH_NAMES = {"asp.net", "vb.net", "ado.net"}  # look like domains but are technologies
_PHONE = re.compile(r"(?<![\w.])\+?\(?\d[\d\s().\-]{7,22}\d(?!\w)")
_YEAR = re.compile(r"(?:19|20)\d{2}")
_NUMERIC_DATE = re.compile(r"\b\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4}\b")

_DATE = (
    r"(?:\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4}"
    r"|\d{4}[./\-]\d{1,2}[./\-]\d{1,2}"
    r"|\d{1,2}(?:st|nd|rd|th)?\s{1,3}[^\W\d_]{3,12}\.?,?\s{1,3}\d{4}"
    r"|[^\W\d_]{3,12}\.?\s{1,3}\d{1,2}(?:st|nd|rd|th)?,?\s{1,3}\d{4})"
)
_DOB = re.compile(
    r"(?i)(?<![^\W_])((?:date\s{1,3}of\s{1,3}birth|birth\s{1,3}date|birthday|dob|born|"
    r"дата\s{1,3}народження|народ[^\W\d_]{0,12})[\s:.\-–]{0,6})" + f"({_DATE})"
)


def _is_phone(match: str) -> bool:
    digits = sum(c.isdigit() for c in match)
    if digits < 9 or digits > 15:
        return False
    if _NUMERIC_DATE.search(match):
        return False
    tokens = re.findall(r"\d+", match)
    if all(_YEAR.fullmatch(t) for t in tokens):  # "2019 2021 2023": year ranges, not a number
        return False
    return True


_CYR_TO_LAT = {
    "а": ["a"], "б": ["b"], "в": ["v"], "г": ["h", "g"], "ґ": ["g"], "д": ["d"], "е": ["e"], "є": ["ie", "ye", "e"],
    "ж": ["zh"], "з": ["z"], "и": ["y", "i"], "і": ["i", "y"], "ї": ["i", "yi", "ji"], "й": ["i", "y"], "к": ["k"],
    "л": ["l"], "м": ["m"], "н": ["n"], "о": ["o"], "п": ["p"], "р": ["r"], "с": ["s"], "т": ["t"], "у": ["u"],
    "ф": ["f"], "х": ["kh", "h"], "ц": ["ts", "c"], "ч": ["ch"], "ш": ["sh"], "щ": ["shch", "sch"], "ь": [""],
    "ю": ["iu", "yu", "u"], "я": ["ia", "ya", "a"], "ъ": [""], "ы": ["y"], "э": ["e"], "ё": ["yo", "e"],
}
_LAT_TOKENS = {
    "shch": ["щ"], "sch": ["щ"], "zh": ["ж"], "kh": ["х"], "ts": ["ц"], "ch": ["ч"], "sh": ["ш"],
    "yu": ["ю"], "iu": ["ю"], "ya": ["я"], "ia": ["я"], "ye": ["є", "е"], "ie": ["є", "іе"], "yi": ["ї"],
    "a": ["а"], "b": ["б"], "c": ["ц", "к"], "d": ["д"], "e": ["е", "є"], "f": ["ф"], "g": ["г", "ґ"],
    "h": ["г", "х"], "i": ["і", "и"], "j": ["й", "дж"], "k": ["к"], "l": ["л"], "m": ["м"], "n": ["н"],
    "o": ["о"], "p": ["п"], "q": ["к"], "r": ["р"], "s": ["с"], "t": ["т"], "u": ["у"], "v": ["в"],
    "w": ["в"], "x": ["кс"], "y": ["и", "й", "і"], "z": ["з"],
}
MAX_VARIANTS = 8


def _variants(options: list[list[str]]) -> list[str]:
    """The base spelling (first option everywhere) plus one variant per alternative at a single position."""
    base = "".join(o[0] for o in options)
    out = [base]
    for i, opts in enumerate(options):
        for alt in opts[1:]:
            v = "".join(o[0] for o in options[:i]) + alt + "".join(o[0] for o in options[i + 1:])
            if v not in out:
                out.append(v)
    return out[:MAX_VARIANTS]


def _transliterations(word: str) -> list[str]:
    """Spelling of a name word in the other script (Ukrainian national transliteration style), a few variants."""
    w = word.lower()
    if re.search(r"[\u0400-\u04ff]", w):
        options = [_CYR_TO_LAT.get(c, [c]) for c in w]
    else:
        options, i = [], 0
        while i < len(w):
            for n in (4, 3, 2, 1):
                chunk = w[i:i + n]
                if chunk in _LAT_TOKENS:
                    options.append(_LAT_TOKENS[chunk])
                    i += n
                    break
            else:
                options.append([w[i]])
                i += 1
    return [v for v in _variants(options) if len(v) >= 2]


def cv_first_line_name(text: str) -> str:
    """The first non-empty line when it looks like a name: 2-4 capitalised words, no digits, @ or colon."""
    if not isinstance(text, str):
        return ""
    for line in text[:MAX_CHARS].splitlines():
        line = line.strip()
        if not line:
            continue
        words = line.split()
        if (len(line) <= 60 and 2 <= len(words) <= 4 and not re.search(r"[\d@:]", line)
                and all(w[0].isupper() for w in words)):
            return line
        return ""
    return ""


def collect_names(text: str, names: Iterable[str] = ()) -> list[str]:
    """The given names plus the name found at the top of the CV text."""
    out = [n for n in (names or ()) if isinstance(n, str)]
    first = cv_first_line_name(text)
    if first:
        out.append(first)
    return out


def _name_pattern(names: Iterable[str]) -> re.Pattern[str] | None:
    words: set[str] = set()
    for name in names or ():
        if not isinstance(name, str):
            continue
        for w in re.findall(r"[^\W\d_]+", name[:200]):
            if len(w) >= 2:
                words.add(w.lower())
    if not words:
        return None
    chosen = sorted(words, key=lambda w: (-len(w), w))[:MAX_NAME_WORDS]
    forms: set[str] = set()
    for w in chosen:
        forms.add(w)
        forms.update(_transliterations(w))
    parts = []
    for f in sorted(forms, key=lambda x: (-len(x), x)):
        if len(f) >= 4:  # inflected forms: Петренко / Петренка, Іван / Івана
            parts.append(re.escape(f[:-1]) + r"[^\W\d_]{0,3}")
        else:
            parts.append(re.escape(f))
    return re.compile(r"(?i)(?<![^\W\d_])(?:" + "|".join(parts) + r")(?![^\W\d_])")


def anonymize_text(text: str, names: Iterable[str] = (), *, first_line_name: bool = True) -> str:
    """The text without emails, phone numbers, links, the given names and the date of birth."""
    if not isinstance(text, str):
        return ""
    out = text[:MAX_CHARS]
    out = _DOB.sub(lambda m: m.group(1) + "[date of birth]", out)
    out = _EMAIL.sub("[email]", out)
    out = _URL.sub("[link]", out)
    out = _DOMAIN.sub(lambda m: m.group(0) if m.group(0).lower() in _TECH_NAMES else "[link]", out)
    out = _PHONE.sub(lambda m: "[phone]" if _is_phone(m.group(0)) else m.group(0), out)
    out = _LABELLED_HANDLE.sub("[link]", out)
    out = _BARE_HANDLE.sub("[link]", out)
    pattern = _name_pattern(collect_names(text, names) if first_line_name else names)
    if pattern is not None:
        out = pattern.sub("[name]", out)
    return out


def anonymize_obj(obj: Any, names: Iterable[str] = ()) -> Any:
    """A copy of dicts, lists and tuples with anonymize_text applied to every string value (and key)."""
    names = tuple(names or ())
    pattern_names = names

    def walk(value: Any, depth: int) -> Any:
        if isinstance(value, str):
            return anonymize_text(value, pattern_names, first_line_name=False)
        if depth >= MAX_DEPTH:
            return None
        if isinstance(value, dict):
            return {(anonymize_text(k, pattern_names, first_line_name=False) if isinstance(k, str) else k): walk(v, depth + 1)
                    for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [walk(v, depth + 1) for v in value]
        return value

    return walk(obj, 0)
