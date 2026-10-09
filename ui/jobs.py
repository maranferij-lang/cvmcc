"""Live job postings under a direction: search, ranking against the CV, honest links for searching yourself."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from urllib.parse import urlsplit

import streamlit as st

from cvmax import config
from cvmax.jobs import JobQuery, deep_links, demo_search_result, rank_jobs, search_jobs
from cvmax.llm import LLMError
from cvmax.safe_text import md_escape
from ui.account import log_event, take_limit
from ui.common import card, get_llm, is_demo, secret

MAX_SHOWN = 20
RANK_NOTE = "Ranking is unavailable right now; showing unranked results."
_LABEL_BAD = re.compile(r"[<>\[\]()*_$`]")
_LABEL_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)


class _SearchFailed(Exception):
    """All sources are unavailable: we do not cache such a result."""

    def __init__(self, result) -> None:
        super().__init__("all job sources failed")
        self.result = result


@st.cache_data(ttl=config.JOBS_CACHE_TTL_S, show_spinner=False)
def _search(keywords: tuple[str, ...], region: str, level: str, company: str):
    """Cached search: arguments are hashed, so we build the query here."""
    result = search_jobs(JobQuery(keywords=keywords, region=region, level=level, company=company), env=os.environ)
    if not result.sources_ok and result.sources_failed:
        raise _SearchFailed(result)  # an exception does not get into the cache
    return result


def _cached_search(keywords: tuple[str, ...], region: str, level: str, company: str):
    try:
        return _search(keywords, region, level, company)
    except _SearchFailed as exc:
        return exc.result


def _role_tag(role: str) -> str:
    """A short fingerprint of the role: HMAC-SHA256 with a server secret (no dictionary guessing).
    Without a key it returns "" and the tag does not go into the event."""
    key = (os.environ.get("CVMAX_EVENT_SALT") or os.environ.get("CVMAX_DB_TOKEN")
           or secret("CVMAX_EVENT_SALT") or secret("CVMAX_DB_TOKEN") or "")
    if not key:
        return ""
    return hmac.new(key.encode(), role.lower().encode(), hashlib.sha256).hexdigest()[:12]


def _tag_fields(role: str) -> dict:
    tag = _role_tag(role)
    return {"role_tag": tag} if tag else {}


def _state_key(key: str, q: JobQuery) -> str:
    """The state key depends on the query: results of another role are not mixed in."""
    raw = repr((q.keywords, q.region, q.level, q.company)).encode()
    return f"jobs_{key}_{hashlib.sha1(raw).hexdigest()[:10]}"

def safe_url(url: str) -> bool:
    """Strict: only http(s), no spaces, with a host and no login in the address."""
    if not isinstance(url, str) or not url.startswith(("https://", "http://")) or re.search(r"\s", url):
        return False
    try:
        parts = urlsplit(url)
        return bool(parts.hostname) and not parts.username
    except ValueError:
        return False


def plain_label(text: str, limit: int = 90) -> str:
    """Button label: plain text without markup."""
    return _LABEL_BAD.sub("", _LABEL_URL.sub("", text)).strip()[:limit]


def _keywords(role: str, keywords: list[str]) -> tuple[str, ...]:
    out: list[str] = []
    for k in [role, *keywords]:
        k = " ".join(str(k).split())
        if k and k.lower() not in (x.lower() for x in out):
            out.append(k)
    return tuple(out[:5])


def _run(key: str, q: JobQuery, *, role: str, region: str, level: str, company: str,
         cv_text: str, gaps: list[str], feedback_language: str) -> None:
    result = demo_search_result(q) if is_demo() else _cached_search(q.keywords, region, level, company)
    note = ""
    try:
        ranked = rank_jobs(get_llm(), cv_text=cv_text, role=role, gaps=gaps, keywords=list(q.keywords),
                           vacancies=result.vacancies, feedback_language=feedback_language)
    except LLMError:
        ranked = [(v, None) for v in result.vacancies]
        note = RANK_NOTE
    st.session_state[_state_key(key, q)] = (result, ranked, note)
    log_event("jobs_shown", **_tag_fields(role), region=region, n=len(result.vacancies), sources=list(result.sources_ok))


def _applied(key: str, vacancy, fit, role: str, region: str) -> None:
    wid = f"applied_{key}_{vacancy.id}"
    sent = st.session_state.setdefault("jobs_applied_sent", set())
    if not st.session_state.get(wid) or wid in sent:
        return  # unchecking and repeated clicks do not count
    sent.add(wid)
    log_event("job_applied", **_tag_fields(role), region=region, source=vacancy.source, fit=fit.fit if fit else None)


def _vacancy(key: str, i: int, vacancy, fit, role: str, region: str) -> None:
    with card(f"job-{key}-{i}"):
        st.link_button(plain_label(f"{vacancy.title} · {vacancy.company}"), vacancy.url, type="secondary")
        parts = [vacancy.location, vacancy.source, vacancy.posted_at, vacancy.salary]
        line = " · ".join(md_escape(str(p)) for p in parts if p)
        if line:
            st.caption(line)
        if fit:
            badge = f":red-badge[fit {fit.fit}]" if fit.fit >= 70 else f":gray-badge[fit {fit.fit}]"
            st.markdown(f"{badge} {md_escape(fit.why)}")
            if fit.missing:
                st.caption("Missing: " + ", ".join(md_escape(m) for m in fit.missing))
        st.checkbox("Applied", key=f"applied_{key}_{vacancy.id}", on_change=_applied,
                    args=(key, vacancy, fit, role, region))


def render_jobs(*, key: str, role: str, keywords: list[str], region: str, level: str, company: str,
                cv_text: str, gaps: list[str], feedback_language: str) -> None:
    """The search button and the list of postings from the session state."""
    kws = _keywords(role, keywords)
    try:
        q = JobQuery(keywords=kws, region=region, level=level, company=company) if kws else None
    except ValueError:  # degenerate keywords (control characters)
        q = None
        st.caption("No searchable role for this direction yet.")
    if q and st.button("Show live vacancies", key=f"jobs_btn_{key}") and take_limit("jobs"):
        with st.spinner("Looking through job boards..."):
            _run(key, q, role=role, region=region, level=level, company=company,
                 cv_text=cv_text, gaps=gaps, feedback_language=feedback_language)

    stored = st.session_state.get(_state_key(key, q)) if q else None
    if stored:
        result, ranked, note = stored
        if note:
            st.info(note)
        shown = [(v, f) for v, f in ranked if safe_url(v.url)][:MAX_SHOWN]
        if not shown:
            st.caption("No matching postings from our sources right now. Try the links below.")
        for i, (v, f) in enumerate(shown):
            _vacancy(key, i, v, f, role, region)
        links = list(result.deep_links)
    else:
        links = deep_links(q) if q else []
    if links:
        st.caption("Search yourself:")
        cols = st.columns(len(links))
        for col, (name, url) in zip(cols, links):
            if safe_url(url):
                col.link_button(plain_label(name, 30), url, type="secondary")
    if stored and stored[0].sources_failed:
        st.caption("Could not reach: " + ", ".join(md_escape(x) for x in stored[0].sources_failed))
