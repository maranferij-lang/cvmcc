"""Models of a job posting and a search query."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

from cvmax.profile import LEVELS, REGIONS

MAX_KEYWORDS = 5
MAX_KEYWORD_LEN = 60
MAX_COMPANY_LEN = 80

# Levels for which we look for internships and first jobs (taken from the profile, we do not duplicate the strings).
ENTRY_LEVELS = (LEVELS[0], LEVELS[1])

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def _clean(value: str) -> str:
    """Removes control characters (replaces them with a space) and extra spaces."""
    text = "".join(" " if unicodedata.category(ch) == "Cc" else ch for ch in value)
    return " ".join(text.split())


def _norm(value: str) -> str:
    """Normalization for deduplication: lower case, no punctuation."""
    return " ".join(_PUNCT.sub(" ", value.lower()).split())


@dataclass(frozen=True)
class Vacancy:
    url: str
    title: str
    company: str
    location: str
    source: str
    posted_at: str | None  # ISO date YYYY-MM-DD
    salary: str | None
    snippet: str  # plain text, up to 400 characters
    remote: bool | None
    tags: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        return hashlib.sha1(self.url.encode("utf-8")).hexdigest()[:12]

    @property
    def key(self) -> str:
        return f"{_norm(self.title)}|{_norm(self.company)}"

    def to_row(self) -> dict:
        return {
            "url": self.url,
            "source": self.source,
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "posted_at": self.posted_at,
            "salary": self.salary,
            "snippet": self.snippet,
            "remote": self.remote,
        }


@dataclass(frozen=True)
class JobQuery:
    keywords: tuple[str, ...]
    region: str
    level: str
    company: str = ""
    remote_ok: bool = False

    def __post_init__(self) -> None:
        raw = self.keywords
        if isinstance(raw, str):
            raw = (raw,)
        raw = tuple(raw)
        if not 1 <= len(raw) <= MAX_KEYWORDS:
            raise ValueError(f"keywords: expected 1..{MAX_KEYWORDS} items")
        cleaned: list[str] = []
        for item in raw:
            if not isinstance(item, str):
                raise ValueError("keywords must be strings")
            word = _clean(item)[:MAX_KEYWORD_LEN].strip()
            if not word:
                raise ValueError("keyword is empty")
            if word.lower() not in (c.lower() for c in cleaned):
                cleaned.append(word)
        if self.region not in REGIONS:
            raise ValueError(f"unknown region: {self.region!r}")
        if self.level not in LEVELS:
            raise ValueError(f"unknown level: {self.level!r}")
        company = _clean(self.company or "")[:MAX_COMPANY_LEN].strip()
        object.__setattr__(self, "keywords", tuple(cleaned))
        object.__setattr__(self, "company", company)
        object.__setattr__(self, "remote_ok", bool(self.remote_ok))

    @property
    def primary(self) -> str:
        return self.keywords[0]

    @property
    def wants_entry_level(self) -> bool:
        return self.level in ENTRY_LEVELS
