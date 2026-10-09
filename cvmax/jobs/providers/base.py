"""Common base of the providers: protocol, RSS/JSON/date parsing, domain allowlist."""
from __future__ import annotations

import html
import json
import re
from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
from urllib.parse import quote_plus, urlsplit

from defusedxml import ElementTree as SafeET

from cvmax.jobs.models import JobQuery, Vacancy
from cvmax.profile import REGIONS

# Regions: exact strings from cvmax.profile.REGIONS.
US = "US / Canada"
UK = "UK"
EU = "EU"
UA = "Ukraine / Eastern Europe"
REMOTE = "Remote, anywhere"
for _r in (US, UK, EU, UA, REMOTE):
    assert _r in REGIONS, _r
ALL_REGIONS = frozenset(REGIONS)

SNIPPET_LEN = 400
# Bounded patterns: no quadratic backtracking on "<script " or an unclosed "<".
_TAG = re.compile(r"<[^<>]*>")
_SKIP_BLOCKS = re.compile(
    r"<(script|style)\b[^<]{0,5000}(?:<(?!/\1)[^<]{0,5000}){0,50}</\1\s*>", re.I)
_MAX_HTML = 20000  # the input is truncated before any regex
_BAD_URL_CHARS = re.compile(r"[\s\\\x00-\x1f\x7f]")


class Provider(Protocol):
    name: str
    kind: str  # "rss" | "json" | "deeplink"
    regions: frozenset[str]
    domains: tuple[str, ...]
    needs: tuple[str, ...]

    def build_urls(self, q: JobQuery, env: Mapping[str, str]) -> list[str]: ...

    def parse(self, body: bytes, url: str, q: JobQuery) -> list[Vacancy]: ...

    def search_url(self, q: JobQuery) -> str | None: ...


class BaseProvider:
    """Default values: by default a provider is only a search link."""

    name = ""
    kind = "deeplink"
    regions: frozenset[str] = ALL_REGIONS
    domains: tuple[str, ...] = ()
    needs: tuple[str, ...] = ()

    def build_urls(self, q: JobQuery, env: Mapping[str, str]) -> list[str]:
        return []

    def parse(self, body: bytes, url: str, q: JobQuery) -> list[Vacancy]:
        return []

    def search_url(self, q: JobQuery) -> str | None:
        return None


def requests_for(provider: Any, q: JobQuery,
                 env: Mapping[str, str]) -> list[tuple[str, Any]]:
    """A list of (url, json_body | None): build_requests if present, otherwise GET addresses."""
    builder = getattr(provider, "build_requests", None)
    if builder is not None:
        return list(builder(q, env))
    return [(u, None) for u in provider.build_urls(q, env)]


def env_value(env: Mapping[str, str], name: str) -> str:
    return str(env.get(name) or "").strip()


def has_needs(provider: Any, env: Mapping[str, str]) -> bool:
    return all(env_value(env, n) for n in provider.needs)


# ---------- text and keywords ----------

def quote_kw(s: str) -> str:
    """A cleaned keyword for the query string."""
    return quote_plus(" ".join(str(s).split()))


def clean_html(s: Any, limit: int | None = SNIPPET_LEN) -> str:
    """HTML -> plain text. Removes tags (including doubly escaped ones), decodes entities."""
    if not isinstance(s, str):
        return ""
    s = s[:_MAX_HTML]
    text = _SKIP_BLOCKS.sub(" ", s)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = _SKIP_BLOCKS.sub(" ", text)  # Greenhouse returns escaped HTML
    text = _TAG.sub(" ", text)
    text = " ".join(text.split())
    if limit is not None and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def as_str(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def matches_query(q: JobQuery, *texts: str) -> bool:
    """True if for some keyword all its words are in the text."""
    hay = " ".join(texts).lower()
    for kw in q.keywords:
        words = [w for w in kw.lower().split() if len(w) >= 2]
        if words and all(w in hay for w in words):
            return True
    return False


def company_slug(q: JobQuery) -> str | None:
    """A company slug for Greenhouse/Lever; None if empty or not allowed."""
    slug = "".join(q.company.lower().split())
    return slug if re.fullmatch(r"[a-z0-9-]{2,40}", slug) else None


# ---------- dates ----------

def parse_date(value: Any) -> str | None:
    """RFC 2822, ISO 8601, YYYY-MM-DD or unix time -> YYYY-MM-DD."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _from_timestamp(float(value))
    s = str(value).strip()
    if not s:
        return None
    if s.isdigit() and len(s) >= 9:
        return _from_timestamp(float(s))
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            return date.fromisoformat(s).isoformat()
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(s).date().isoformat()
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def _from_timestamp(ts: float) -> str | None:
    if ts > 1e11:  # milliseconds
        ts /= 1000
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


# ---------- URL allowlist ----------

def url_host(url: Any) -> str | None:
    """The host for an http(s) address without userinfo/spaces/a non-standard port, otherwise None."""
    if not isinstance(url, str) or not url or len(url) > 2000:
        return None
    if _BAD_URL_CHARS.search(url):
        return None
    try:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            return None
        if parts.username is not None or parts.password is not None or "@" in parts.netloc:
            return None
        if parts.port is not None:
            return None
        host = (parts.hostname or "").rstrip(".").lower()
    except ValueError:
        return None
    return host or None


def http_url_ok(url: Any) -> bool:
    """Any safe http(s) host (only for aggregators like Jooble)."""
    return url_host(url) is not None


def allowed_url(url: Any, domains: tuple[str, ...]) -> bool:
    host = url_host(url)
    if host is None:
        return False
    return any(host == d or host.endswith("." + d) for d in domains)


def make_vacancy(*, url: Any, title: Any, company: Any = "", location: Any = "",
                 source: str, domains: tuple[str, ...] = (),
                 posted_at: str | None = None, salary: Any = None,
                 snippet: Any = "", remote: bool | None = None,
                 tags: Any = (), allow: Callable[[str], bool] | None = None
                 ) -> Vacancy | None:
    """Builds a Vacancy; None if the address does not pass the allowlist or there is no title."""
    ok = allow(url) if allow is not None else allowed_url(url, domains)
    if not ok:
        return None
    clean_title = clean_html(title, 200)
    if not clean_title:
        return None
    sal = clean_html(salary, 80) if salary else ""
    tag_list = [t for t in (clean_html(x, 40) for x in (tags or ()) if isinstance(x, str)) if t]
    return Vacancy(
        url=str(url).strip(),
        title=clean_title,
        company=clean_html(company, 100),
        location=clean_html(location, 120),
        source=source,
        posted_at=posted_at,
        salary=sal or None,
        snippet=clean_html(snippet, SNIPPET_LEN),
        remote=remote,
        tags=tuple(tag_list[:8]),
    )


# ---------- parsing formats ----------

def parse_json(body: Any) -> Any | None:
    try:
        return json.loads(body)
    except (ValueError, TypeError, RecursionError):
        return None


def dict_items(obj: Any, key: str | None = None) -> list[dict]:
    """A list of dicts from obj (or from obj[key]); everything else is dropped."""
    if key is not None:
        obj = obj.get(key) if isinstance(obj, dict) else None
    return [x for x in obj if isinstance(x, dict)] if isinstance(obj, list) else []


def _local(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _text(el: Any) -> str:
    return "".join(el.itertext()).strip()


def parse_rss(body: bytes) -> list[dict]:
    """RSS 2.0 <item> or Atom <entry> -> dict(title, link, description, pubDate, guid)."""
    try:
        root = SafeET.fromstring(body, forbid_dtd=True)
    except Exception:
        return []
    items: list[dict] = []
    for el in root.iter():
        name = _local(el.tag)
        if name not in ("item", "entry"):
            continue
        row = {"title": "", "link": "", "description": "", "pubDate": "", "guid": ""}
        for child in el:
            tag = _local(child.tag)
            if tag == "title":
                row["title"] = _text(child)
            elif tag == "link":
                href = child.get("href")
                if href:
                    if child.get("rel") in (None, "alternate") or not row["link"]:
                        row["link"] = href.strip()
                elif not row["link"]:
                    row["link"] = _text(child)
            elif tag in ("description", "summary", "content") and not row["description"]:
                row["description"] = _text(child)
            elif tag in ("pubDate", "published", "updated", "date") and not row["pubDate"]:
                row["pubDate"] = _text(child)
            elif tag in ("guid", "id"):
                row["guid"] = _text(child)
        items.append(row)
    return items
