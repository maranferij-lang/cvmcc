"""Loading and validating the hand-curated resource catalog and the skill vocabulary.

Nothing here talks to the network. A malformed data file is never skipped silently: it raises CatalogError
that names the file and the reason. Semantic rules live in validate() and return a list of error strings.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import re
import socket
from datetime import date
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlsplit

from .models import (
    COSTS, KINDS, LEVELS, MAX_PRICE_NOTE, MAX_WHY_PICKED, STATUSES, VERIFICATIONS, Resource, ResourceFormatError,
    Skill,
)

log = logging.getLogger(__name__)

RESOURCES_DIR = Path(__file__).resolve().parent
CATALOG_DIR = RESOURCES_DIR / "catalog"
SKILLS_PATH = RESOURCES_DIR / "skills.json"
LINK_STATUS_PATH = RESOURCES_DIR / "link_status.json"

MAX_AGE_DAYS = 400
MIN_RATING_COUNT = 1000
MIN_RATING_SHARE = 0.9
EVIDENCE_NEEDED = {"free": 1, "free-audit": 1, "freemium": 2, "paid": 2}

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_LANG_RE = re.compile(r"^[a-z]{2,3}$")
_ID_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Hosts that only resolve inside a network: never a public learning resource.
PRIVATE_HOST_SUFFIXES = (".internal", ".local", ".localhost", ".lan")

# Link shorteners hide the real destination, so they are never accepted.
SHORTENER_HOSTS = frozenset({
    "bit.ly", "t.co", "tinyurl.com", "goo.gl", "ow.ly", "is.gd", "buff.ly", "rebrand.ly", "cutt.ly", "t.ly",
    "shorturl.at", "lnkd.in", "rb.gy", "tiny.cc", "bit.do", "soo.gd", "s.id", "amzn.to", "a.co", "fb.me",
    "lnk.to", "v.gd", "shorte.st", "adf.ly", "bl.ink", "short.io", "trib.al", "dlvr.it",
})

# Known affiliate and click-tracking redirectors. A host matches when it equals one of these or is a subdomain.
AFFILIATE_HOSTS = frozenset({
    "click.linksynergy.com", "linksynergy.com", "go.skimresources.com", "skimresources.com", "skimlinks.com",
    "awin1.com", "zenaps.com", "shareasale.com", "shareasale-analytics.com",
    "goto.impact.com", "d.impact.com", "track.impact.com", "click.impact.com", "impactradius.com",
    "impactradius-go.com", "sjv.io", "pxf.io", "ojrq.net", "prf.hn", "tkqlhce.com", "jdoqocy.com",
    "anrdoezrs.net", "dpbolvw.net", "kqzyfj.com", "go.redirectingat.com", "redirectingat.com",
    "avantlink.com", "clickbank.net", "tidd.ly", "partnerstack.com",
})

# Query parameters that mark a tracked or affiliate link.
_BAD_QUERY_KEYS = frozenset({"ref", "affid", "aff_id", "utm_source"})
_BAD_QUERY_SUBSTRINGS = ("affid", "aff_id")


class CatalogError(Exception):
    """A catalog or vocabulary file cannot be read or has the wrong structure."""


def _read_json(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise CatalogError(f"{path.name}: cannot read file ({type(e).__name__})") from e
    except UnicodeDecodeError as e:
        raise CatalogError(f"{path.name}: not valid UTF-8 ({e.reason})") from e
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise CatalogError(f"{path.name}: invalid JSON ({e.msg} at line {e.lineno} column {e.colno})") from e


def load_catalog(path: str | Path | None = None) -> list[Resource]:
    """Loads every resource from the *.json files in a directory (default: cvmax/resources/catalog).

    Returns [] when the directory is missing or has no json files. A single file path is accepted too.
    A malformed file raises CatalogError naming the file and the reason. Files load in name order.
    """
    base = Path(path) if path is not None else CATALOG_DIR
    if base.is_file():
        files = [base]
    elif base.is_dir():
        files = sorted(p for p in base.glob("*.json") if p.is_file())
    else:
        files = []
    resources: list[Resource] = []
    for f in files:
        data = _read_json(f)
        if not isinstance(data, dict):
            raise CatalogError(f"{f.name}: top level must be an object with 'cluster' and 'resources'")
        if not isinstance(data.get("cluster"), str) or not data["cluster"]:
            raise CatalogError(f"{f.name}: missing or invalid 'cluster'")
        items = data.get("resources")
        if not isinstance(items, list):
            raise CatalogError(f"{f.name}: 'resources' must be a list")
        for i, item in enumerate(items):
            try:
                resources.append(Resource.from_dict(item))
            except ResourceFormatError as e:
                raise CatalogError(f"{f.name}: resources[{i}]: {e}") from e
    return resources


def load_skills(path: str | Path | None = None) -> list[Skill]:
    """Loads the skill vocabulary (default: cvmax/resources/skills.json). Raises CatalogError if malformed."""
    p = Path(path) if path is not None else SKILLS_PATH
    if not p.is_file():
        raise CatalogError(f"{p.name}: file not found")
    data = _read_json(p)
    items = data.get("skills") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise CatalogError(f"{p.name}: top level must be an object with a 'skills' list")
    skills: list[Skill] = []
    for i, item in enumerate(items):
        where = f"{p.name}: skills[{i}]"
        if not isinstance(item, dict):
            raise CatalogError(f"{where}: must be an object")
        for key in ("id", "label", "aliases", "programs"):
            if key not in item:
                raise CatalogError(f"{where}: missing field {key!r}")
        if not isinstance(item["id"], str) or not item["id"]:
            raise CatalogError(f"{where}: 'id' must be a non-empty string")
        if not isinstance(item["label"], str):
            raise CatalogError(f"{where}: 'label' must be a string")
        for key in ("aliases", "programs"):
            if not isinstance(item[key], list) or not all(isinstance(x, str) for x in item[key]):
                raise CatalogError(f"{where}: {key!r} must be a list of strings")
        skills.append(Skill(id=item["id"], label=item["label"], aliases=list(item["aliases"]),
                            programs=list(item["programs"])))
    return skills


def load_link_status(path: str | Path | None = None) -> dict:
    """Loads link_status.json ({id: {"ok": bool, "code": int|None, "checked": str, "fails": int}}).

    Returns {} when the file is missing or unreadable: the checker may not have run yet, and the page must not break.
    """
    p = Path(path) if path is not None else LINK_STATUS_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def visible_resources(resources: Iterable[Resource], link_status: dict | None = None) -> list[Resource]:
    """Drops entries that are not active or whose link check says dead.

    link_status entries look like {"ok": False, "code": 404, "checked": "2026-10-12", "fails": 2}: a link is dead
    when ok is False and fails >= 2 (a first failure is not enough). Entries with status "dead" also count.
    Unknown ids and unknown shapes keep the resource visible.
    """
    status = link_status or {}
    return [r for r in resources if r.status == "active" and not _is_dead(status.get(r.id))]


def _is_dead(entry: Any) -> bool:
    if isinstance(entry, str):
        return entry == "dead"
    if not isinstance(entry, dict):
        return False
    if entry.get("status") == "dead" or entry.get("dead") is True:
        return True
    if entry.get("ok") is not False:
        return False
    fails = entry.get("fails")
    # The checker marks a link dead after 2 consecutive failures; a missing counter is treated as dead.
    return not isinstance(fails, int) or fails >= 2


# --- validation ---

def _is_numeric_ipv4(host: str) -> bool:
    """True for every form the C resolver reads as an IPv4 address: 127.1, 0177.0.0.1, 0x7f.1, 2130706433."""
    try:
        socket.inet_aton(host)
        return True
    except (OSError, ValueError, UnicodeError):
        return False


def _host_error(host: str) -> str | None:
    """Returns why a host is not acceptable, or None."""
    h = host.lower().rstrip(".")
    if not h:
        return "url has no host"
    try:
        ipaddress.ip_address(h.strip("[]"))
        return f"host {h} is an IP address"
    except ValueError:
        pass
    if re.fullmatch(r"\d+|0x[0-9a-f]+", h) or _is_numeric_ipv4(h):
        return f"host {h} is a numeric IP address"
    if "." not in h:
        return f"host {h} is not a public domain name"
    if h.endswith(PRIVATE_HOST_SUFFIXES):
        return f"host {h} is a private network name"
    if h in SHORTENER_HOSTS:
        return f"host {h} is a link shortener"
    for bad in AFFILIATE_HOSTS:
        if h == bad or h.endswith("." + bad):
            return f"host {h} is an affiliate redirector"
    return None


def url_error(url: Any, *, check_query: bool = True) -> str | None:
    """Returns why a catalog url is not acceptable, or None. https only, no IP, shortener or affiliate link."""
    if not isinstance(url, str) or not url:
        return "url must be a non-empty string"
    if url != url.strip() or any(c.isspace() for c in url):
        return "url contains whitespace"
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        parts.port  # noqa: B018 - raises ValueError on a bad port
    except ValueError:
        return "url is malformed"
    if parts.scheme != "https":
        return "url must use https"
    if parts.username is not None or parts.password is not None:
        return "url must not contain credentials"
    err = _host_error(host)
    if err:
        return err
    if check_query:
        query = parts.query.lower()
        keys = {k.lower() for k, _ in parse_qsl(parts.query, keep_blank_values=True)}
        if keys & _BAD_QUERY_KEYS or any(s in query for s in _BAD_QUERY_SUBSTRINGS) or "utm_source" in query:
            return "url has a tracking or affiliate query parameter"
    return None


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _skill_ids(skills: Iterable[Any]) -> set[str]:
    return {s.id if isinstance(s, Skill) else str(s) for s in skills}


def slug(text: str) -> str:
    """Lower-case ASCII slug of a title or provider."""
    return "-".join(_ID_TOKEN_RE.findall(text.lower()))


def validate(resources: Iterable[Resource], skills: Iterable[Any], today: date | None = None) -> list[str]:
    """Checks every rule of the spec and returns error strings ("<id>: <problem>"); [] means the catalog is clean.

    skills is the vocabulary (Skill objects or plain ids). today is injectable for tests; default date.today().
    """
    today = today or date.today()
    known = _skill_ids(skills)
    errors: list[str] = []
    seen_ids: dict[str, int] = {}
    for idx, r in enumerate(resources):
        rid = r.id if isinstance(r.id, str) and r.id else f"<index {idx}>"
        errors.extend(f"{rid}: {msg}" for msg in _validate_one(r, known, today))
        if isinstance(r.id, str) and r.id:
            if r.id in seen_ids:
                errors.append(f"{rid}: duplicate id")
            seen_ids[r.id] = idx
    return errors


def _validate_one(r: Resource, known: set[str], today: date) -> list[str]:
    e: list[str] = []

    def text_field(name: str) -> bool:
        v = getattr(r, name)
        if not isinstance(v, str) or not v.strip():
            e.append(f"{name} must be a non-empty string")
            return False
        return True

    ok_title, ok_provider = text_field("title"), text_field("provider")
    text_field("output")

    # id: lower-case slug that is derived from the title or provider (shares at least one word with them).
    if not isinstance(r.id, str) or not _SLUG_RE.match(r.id):
        e.append("id must be a lower-case slug (a-z, 0-9, hyphens)")
    elif ok_title and ok_provider:
        words = set(_ID_TOKEN_RE.findall(f"{r.title} {r.provider}".lower()))
        if not words & set(r.id.split("-")):
            e.append("id must be derived from the title or provider")

    uerr = url_error(r.url)
    if uerr:
        e.append(uerr)

    for name, allowed in (("kind", KINDS), ("level", LEVELS), ("cost", COSTS),
                          ("verification", VERIFICATIONS), ("status", STATUSES)):
        if getattr(r, name) not in allowed:
            e.append(f"{name} must be one of {', '.join(allowed)} (got {getattr(r, name)!r})")
    if r.status in STATUSES and r.status != "active":
        e.append(f"status must be active for a shipped resource (got {r.status!r})")

    # skills
    if not isinstance(r.skills, list) or not all(isinstance(s, str) for s in r.skills):
        e.append("skills must be a list of strings")
    else:
        if not 1 <= len(r.skills) <= 3:
            e.append("skills must have 1 to 3 items")
        if len(set(r.skills)) != len(r.skills):
            e.append("skills must not repeat")
        for s in r.skills:
            if s not in known:
                e.append(f"unknown skill id {s!r}")

    # cost and price
    if r.cost in ("paid", "freemium"):
        if not _is_num(r.price_usd) or r.price_usd < 0 or (r.cost == "paid" and r.price_usd <= 0):
            e.append(f"cost {r.cost} needs a price_usd number (paid: above 0)")
    elif r.cost in ("free", "free-audit") and r.price_usd is not None:
        e.append(f"cost {r.cost} must have price_usd null")

    # price_note: what the price buys, shown next to the number ("per month with Coursera Plus").
    note = r.price_note
    if not isinstance(note, str):
        e.append("price_note must be a string")
    elif r.cost in ("paid", "freemium") and _is_num(r.price_usd):
        if not note.strip():
            e.append(f"cost {r.cost} needs a price_note that says what the price covers (for example 'per month')")
        elif len(note) > MAX_PRICE_NOTE:
            e.append(f"price_note is {len(note)} characters, the limit is {MAX_PRICE_NOTE}")
    elif r.cost in ("free", "free-audit") and note:
        e.append(f"cost {r.cost} must have an empty price_note")
    elif len(note) > MAX_PRICE_NOTE:
        e.append(f"price_note is {len(note)} characters, the limit is {MAX_PRICE_NOTE}")

    # hours, languages, hands_on, year
    if not _is_int(r.hours) or not 1 <= r.hours <= 400:
        e.append("hours must be an integer between 1 and 400")
    if (not isinstance(r.languages, list) or not r.languages
            or not all(isinstance(x, str) and _LANG_RE.match(x) for x in r.languages)):
        e.append("languages must be a non-empty list of lower-case language codes")
    if not isinstance(r.hands_on, bool):
        e.append("hands_on must be true or false")
    if r.updated_year is not None and (not _is_int(r.updated_year) or not 1990 <= r.updated_year <= today.year):
        e.append("updated_year must be null or a year not in the future")

    # why_picked
    if not isinstance(r.why_picked, str) or not r.why_picked.strip():
        e.append("why_picked must be a non-empty string")
    elif len(r.why_picked) > MAX_WHY_PICKED:
        e.append(f"why_picked is {len(r.why_picked)} characters, the limit is {MAX_WHY_PICKED}")

    # evidence
    if not isinstance(r.evidence, list):
        e.append("evidence must be a list")
    else:
        need = EVIDENCE_NEEDED.get(r.cost, 1)
        if len(r.evidence) < need:
            e.append(f"needs at least {need} evidence item(s) for cost {r.cost} (has {len(r.evidence)})")
        for i, ev in enumerate(r.evidence):
            if not isinstance(ev.claim, str) or not ev.claim.strip():
                e.append(f"evidence[{i}].claim must be a non-empty string")
            if not isinstance(ev.url, str) or not ev.url.startswith("https://") or url_error(ev.url, check_query=False):
                e.append(f"evidence[{i}].url must be a valid https url")
            seen = _parse_date(ev.seen)
            if seen is None:
                e.append(f"evidence[{i}].seen must be an ISO date")
            elif seen > today:
                e.append(f"evidence[{i}].seen is in the future")

    # rating
    if r.rating is not None:
        g = r.rating
        if not _is_num(g.value) or not _is_num(g.scale) or g.scale <= 0 or g.value < 0 or g.value > g.scale:
            e.append("rating value and scale must be numbers with 0 <= value <= scale")
        elif g.value / g.scale < MIN_RATING_SHARE - 1e-9:
            e.append(f"rating {g.value}/{g.scale} is below {MIN_RATING_SHARE:.0%} of the scale")
        if not _is_int(g.count) or g.count < MIN_RATING_COUNT:
            e.append(f"rating count must be at least {MIN_RATING_COUNT}")
        if not isinstance(g.source_url, str) or not g.source_url.startswith("https://") or url_error(g.source_url, check_query=False):
            e.append("rating source_url must be a valid https url")
        seen = _parse_date(g.seen)
        if seen is None:
            e.append("rating seen must be an ISO date")
        elif seen > today:
            e.append("rating seen is in the future")

    # freshness
    verified = _parse_date(r.verified_at)
    if verified is None:
        e.append("verified_at must be an ISO date (YYYY-MM-DD)")
    elif verified > today:
        e.append("verified_at is in the future")
    elif (today - verified).days > MAX_AGE_DAYS:
        e.append(f"verified_at is {(today - verified).days} days old, the limit is {MAX_AGE_DAYS}")
    return e
