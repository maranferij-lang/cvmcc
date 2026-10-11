"""Deterministic resource selection: gap text to skill ids, quality points, and the best few picks. No model.

The rules are in docs/plan-resources-and-free-launch.md, Part E "Selection".
"""
from __future__ import annotations

import re
from datetime import date
from functools import lru_cache
from typing import Iterable

from .catalog import CATALOG_DIR, LINK_STATUS_PATH, SKILLS_PATH, load_catalog, load_link_status, load_skills
from .models import Pick, Resource, Skill, Votes

MAX_MATCHED_SKILLS = 3
MAX_TEXT_CHARS = 5000
MIN_VOTES = 20
FRESH_YEARS = 3
MAIN_TOPIC_POINTS = 3

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_LANG_CODES = {"ukrainian": "uk", "українська": "uk", "uk": "uk", "english": "en", "en": "en"}
_LANG_NAMES = {"uk": "Ukrainian", "en": "English"}


# --- skill matching ---

_NON_ASCII = re.compile(r"[^\x00-\x7f]")


def _alias_regex(alias: str) -> re.Pattern[str]:
    """Word-boundary pattern for an alias. Linear.

    Latin aliases: no letter or digit directly before or after.
    Cyrillic (any non-ASCII) aliases are written as stems because Ukrainian inflects: each word may continue with
    more letters ("статистик" matches "статистика", "статистики"; "бухгалтерськ облік" matches "бухгалтерського
    обліку"), and the match must start at a word start.
    """
    if _NON_ASCII.search(alias):
        words = [re.escape(w) + r"\w*" for w in alias.split()]
        return re.compile(r"(?<!\w)" + r"[\s-]+".join(words))
    return re.compile(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])")


# Words in front of a lone capital "R" that make it a label, not the language ("section R of the form").
_R_LABEL_BEFORE = re.compile(
    r"(?:section|part|appendix|annex|schedule|form|table|figure|fig|exhibit|row|column|class|grade|type|plan|option|"
    r"rule|article|chapter|item|step|line|box|point|group|tier|category|level|series|zone|room|unit|block|stage|"
    r"phase)\s+$",
    re.I,
)
# A capitalised verb at the start of a sentence: "React to feedback quickly", "Excel at communication".
_VERB_AFTER = re.compile(
    r"\s+(?:to|at|in|as|on|under|during|when|while|by|quickly|fast|promptly|positively)\b", re.I
)


def _r_is_language(text: str, m: re.Match[str]) -> bool:
    return _R_LABEL_BEFORE.search(text[max(0, m.start() - 16):m.start()]) is None


def _is_sentence_verb(text: str, m: re.Match[str]) -> bool:
    head = text[:m.start()].rstrip(" \t")
    at_start = not head or head[-1] in ".!?;:\n\u2022*-\u2013\u2014\u00b7"
    return at_start and _VERB_AFTER.match(text, m.end()) is not None


def _word_is_skill(text: str, m: re.Match[str]) -> bool:
    return not _is_sentence_verb(text, m)


# Skill names that are also everyday words are matched case-sensitively on the original text, never inside
# "R&D" or "R/3", so verbs and labels do not match: "react to feedback", "excel at", "section R", "R-squared".
# The checker gets (text, match) and returns False to reject a match. "REACT"/"EXCEL" in capitals also match.
_CASED: dict[str, tuple[re.Pattern[str], object]] = {
    "r": (re.compile(r"(?<![\w&/+#.])R(?![\w&/+#-])"), _r_is_language),
    "react": (re.compile(r"(?<!\w)(?:React|REACT)(?!\w)"), _word_is_skill),
    "excel": (re.compile(r"(?<!\w)(?:Excel|EXCEL)(?!\w)"), _word_is_skill),
}


class _Index:
    """Alias index built once from the vocabulary: (pattern, alias, skill ids) sorted longest alias first."""

    def __init__(self, skills: Iterable[Skill]):
        by_alias: dict[str, set[str]] = {}
        self.single: list[tuple[re.Pattern[str], str, object]] = []
        for s in skills:
            names = {s.id.lower(), s.label.lower(), *(a.lower() for a in s.aliases)}
            for name in names:
                name = name.strip()
                if name in _CASED:
                    pattern, check = _CASED[name]
                    self.single.append((pattern, s.id, check))
                    continue
                if len(name) == 1:
                    continue
                if name:
                    by_alias.setdefault(name, set()).add(s.id)
        self.single.sort(key=lambda t: (t[1], t[0].pattern))
        ordered = sorted(by_alias, key=lambda a: (-len(a), a))
        self.entries = [(_alias_regex(a), a, sorted(by_alias[a])) for a in ordered]


@lru_cache(maxsize=8)
def _index_for(path: str, mtime_ns: int) -> _Index:
    return _Index(load_skills(path))


def _default_index() -> _Index:
    try:
        mtime = SKILLS_PATH.stat().st_mtime_ns
    except OSError:
        mtime = 0
    return _index_for(str(SKILLS_PATH), mtime)


def match_skills(text: str, skills: Iterable[Skill] | None = None) -> list[str]:
    """Maps free text (a gap sentence) to skill ids by alias match on word boundaries.

    Longest alias first (ties by alias text, then skill id), each text span is used once, at most 3 ids.
    Returns [] for text with no known skill. skills overrides the vocabulary file (tests).
    """
    if not isinstance(text, str) or not text.strip():
        return []
    text = text[:MAX_TEXT_CHARS]
    index = _Index(skills) if skills is not None else _default_index()
    lowered = text.lower()
    taken: list[tuple[int, int]] = []
    found: list[str] = []

    def free(span: tuple[int, int]) -> bool:
        return all(span[1] <= a or span[0] >= b for a, b in taken)

    for pattern, _alias, ids in index.entries:
        for m in pattern.finditer(lowered):
            span = m.span()
            if not free(span):
                continue
            taken.append(span)
            for sid in ids:
                if sid not in found:
                    found.append(sid)
            if len(found) >= MAX_MATCHED_SKILLS:
                return found[:MAX_MATCHED_SKILLS]
    for pattern, sid, check in index.single:
        if sid in found:
            continue
        if any(check(text, m) for m in pattern.finditer(text)):
            found.append(sid)
    return found[:MAX_MATCHED_SKILLS]


# --- quality points ---

def normalize_language(language: str | None) -> str | None:
    """'Ukrainian' -> 'uk', 'English' -> 'en'; other values are lower-cased; empty -> None."""
    if not language or not isinstance(language, str):
        return None
    key = language.strip().lower()
    return _LANG_CODES.get(key, key) or None


def _month_year(iso: str) -> str:
    try:
        d = date.fromisoformat(iso)
        return f"{_MONTHS[d.month - 1]} {d.year}"
    except (ValueError, TypeError):
        return str(iso)


def _num(v: float) -> str:
    return f"{v:g}"


def quality_points(
    r: Resource,
    votes: Votes | None = None,
    *,
    level: str | None = None,
    language: str | None = None,
    today: date | None = None,
    skill: str | None = None,
) -> tuple[int, list[str]]:
    """Points and the human-readable reasons for one resource.

    free +3, free-audit +2, freemium +1; hands-on +2; evidence count capped at 3, one point each;
    rating with count >= 5000 and value/scale >= 0.92 gives +2, else count >= 1000 gives +1;
    updated within 3 years +1; language match +2; level match +1;
    user votes (only with at least 20): helpful share >= 0.8 gives +2, below 0.5 gives -3.
    With skill given, +3 when it is the resource's main topic (skills[0]), so a dedicated course outranks a
    resource that only touches the skill.
    """
    today = today or date.today()
    points = 0
    reasons: list[str] = []

    if r.cost == "free":
        points += 3
        reasons.append("Free")
    elif r.cost == "free-audit":
        points += 2
        reasons.append("Free to audit, the certificate costs extra")
    elif r.cost == "freemium":
        points += 1
        reasons.append(f"Free tier, ${_num(r.price_usd)} {r.price_note}".rstrip() if r.price_usd else "Free tier")
    elif r.cost == "paid":
        reasons.append(f"Paid, ${_num(r.price_usd)} {r.price_note}".rstrip() if r.price_usd else "Paid")

    if r.hands_on:
        points += 2
        reasons.append("Hands-on")

    n_ev = min(len(r.evidence), 3)
    if n_ev:
        points += n_ev
        reasons.append(f"{n_ev} source{'s' if n_ev != 1 else ''} checked by a person")

    g = r.rating
    if g is not None and g.scale:
        share = g.value / g.scale
        if g.count >= 5000 and share >= 0.92 - 1e-9:  # tolerance for float division, e.g. 4.6 / 5
            points += 2
        elif g.count >= 1000:
            points += 1
        if g.count >= 1000:
            shown = _num(g.value) if g.scale == 5 else f"{_num(g.value)}/{_num(g.scale)}"
            reasons.append(f"{shown} from {g.count:,} ratings (seen {_month_year(g.seen)})")

    if r.updated_year is not None and today.year - FRESH_YEARS <= r.updated_year <= today.year:
        points += 1
        reasons.append(f"Updated {r.updated_year}")

    lang = normalize_language(language)
    if lang and lang in r.languages:
        points += 2
        reasons.append(f"In {_LANG_NAMES.get(lang, lang)}")

    if level and r.level == level:
        points += 1
        reasons.append(f"Fits your level ({level})")

    if skill is not None and r.skills and r.skills[0] == skill:
        points += MAIN_TOPIC_POINTS
        reasons.append("Main topic")

    if votes is not None and votes.total >= MIN_VOTES:
        pct = round(votes.share * 100)
        if votes.share >= 0.8:
            points += 2
            reasons.append(f"{pct}% of {votes.total} users found it helpful")
        elif votes.share < 0.5:
            points -= 3
            reasons.append(f"Only {pct}% of {votes.total} users found it helpful")
    return points, reasons


# --- default catalog (cached by file modification times) ---

def _signature(directory) -> tuple:
    sig: list = [("dir", str(directory))]
    try:
        files = sorted(directory.glob("*.json")) if directory.is_dir() else []
        sig += [(f.name, f.stat().st_mtime_ns) for f in files]
    except OSError:
        pass
    try:
        sig.append(("link_status", str(LINK_STATUS_PATH), LINK_STATUS_PATH.stat().st_mtime_ns))
    except OSError:
        sig.append(("link_status", str(LINK_STATUS_PATH), 0))
    return tuple(sig)


@lru_cache(maxsize=2)
def _catalog_for(signature: tuple) -> tuple[list[Resource], dict]:
    return load_catalog(CATALOG_DIR), load_link_status(LINK_STATUS_PATH)


def default_catalog() -> list[Resource]:
    """Visible resources of the shipped catalog: active and not dead in link_status.json. Cached per file mtime."""
    from .catalog import visible_resources

    resources, status = _catalog_for(_signature(CATALOG_DIR))
    return visible_resources(resources, status)


# --- pick ---

def pick(
    skill: str,
    *,
    budget: str = "free",
    level: str | None = None,
    language: str | None = None,
    limit: int = 3,
    catalog: Iterable[Resource] | None = None,
    votes: dict[str, Votes] | None = None,
    today: date | None = None,
) -> list[Pick]:
    """Best resources for one skill, best first. Deterministic: points, then id.

    budget "free" drops paid resources. budget "any" allows at most one paid resource and always keeps the best
    free one first when a free one exists. Only status "active" resources are returned. catalog defaults to the
    shipped catalog (link-health filtered); votes maps resource id to Votes (None until the weekly job ran).
    """
    if limit <= 0:
        return []
    pool = default_catalog() if catalog is None else list(catalog)
    scored: list[Pick] = []
    wants_english = normalize_language(language) == "en"
    for r in pool:
        if r.status != "active" or skill not in r.skills:
            continue
        if budget != "any" and r.cost == "paid":
            continue
        # A reader of English recommendations is never sent to a resource that exists only in Ukrainian
        # (for example a translated edition of a text that also has an English original).
        if wants_english and "en" not in r.languages:
            continue
        pts, reasons = quality_points(r, (votes or {}).get(r.id), level=level, language=language, today=today,
                                  skill=skill)
        scored.append(Pick(resource=r, points=pts, reasons=reasons))
    scored.sort(key=lambda p: (-p.points, p.resource.id))

    if budget == "any":
        kept: list[Pick] = []
        paid_taken = False
        for p in scored:
            if p.resource.cost == "paid":
                if paid_taken:
                    continue
                paid_taken = True
            kept.append(p)
        best_free = next((p for p in kept if p.resource.cost != "paid"), None)
        if best_free is not None and kept[0] is not best_free:
            kept.remove(best_free)
            kept.insert(0, best_free)
        scored = kept
    return scored[:limit]
