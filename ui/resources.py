"""Vetted learning resources in the UI: cards, the plan, the two controls and the "How we pick resources" text.

Shared by the "Skills to build" tab of the CV review and by the Learn page. Everything shown comes from the
hand-curated catalog (cvmax.resources) and passes through md_escape; every link is checked again at render time.
Events carry skill ids and resource ids only, never CV text.
"""

from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import urlsplit

import streamlit as st

from cvmax.learning import events
from cvmax.resources import CatalogError, Pick, build_plan, pick, url_error
from cvmax.resources import select as select_mod
from cvmax.safe_text import md_escape
from ui.account import log_event

NO_RESOURCE = "We don't have a vetted resource for this yet."
SOFT_ERROR = "The list of learning resources is not available right now. Everything else works; try again later."
NOT_SPONSORED = "Not sponsored. No affiliate links."

BUDGETS = {"Free only": "free", "Free and paid": "any"}
HOURS_OPTIONS = (3, 5, 10)
DEFAULT_HOURS = 5
MAX_CARDS = 3  # cards shown for one skill
ALL = 1000  # a limit larger than any skill's resource count: count what pick() would return
UNMATCHED = "unmatched"  # resource_missing value when a gap matches no skill; gap text is never logged

PRINCIPLES = (
    "**A person checked every resource.** Each one has a verification date, a short reason why we picked it "
    "and the evidence we saw.",
    "**Free first.** A paid resource appears only when it is clearly better than the free options for that "
    "skill, and it always shows its price.",
    "**No sponsors and no affiliate links.** If that ever changes, affiliate links will carry a visible label "
    "and will never change the ranking.",
    "**A track record.** A recognised institution or vendor, public ratings with a real number of reviews, or a "
    "recommendation from a university or government career service. A \"best courses\" blog post is never evidence.",
    "**Hands-on beats video-only.** Every plan ends with something you can put on your CV.",
    "**If we have nothing vetted, we say so.** We never fill the gap with a search link or a link written by a model.",
    "**Links are re-checked every week.** Broken ones are hidden automatically.",
)

_COST_LABEL = {"free": "Free", "free-audit": "Free to audit", "freemium": "Freemium", "paid": "Paid"}
_EVIDENCE_METHOD = {"fetched": "we opened the page", "search-snippet": "we saw it in a search result"}
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# --- data access (the single place tests replace to use a fixture catalog) ---

def get_catalog() -> list:
    """Visible resources of the shipped catalog. Raises CatalogError when a data file is broken."""
    return select_mod.default_catalog()


# --- small helpers ---

def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-") or "x"


def _price(value: float | None) -> str:
    return "" if value is None else f"${value:g}"


def _month_year(iso: str) -> str:
    m = re.fullmatch(r"(\d{4})-(\d{2})-\d{2}", str(iso))
    if not m or not 1 <= int(m.group(2)) <= 12:
        return md_escape(str(iso))
    return f"{_MONTHS[int(m.group(2)) - 1]} {m.group(1)}"


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _catalog_hosts() -> dict[str, str]:
    """Resource id -> the host stored in the catalog entry."""
    return {r.id: _host(r.url) for r in get_catalog()}


def resource_link_ok(resource) -> bool:
    """The resource link is rendered only when it is https, passes the catalog rules and keeps the catalog host."""
    try:
        expected = _catalog_hosts().get(resource.id)
    except CatalogError:
        return False
    url = resource.url
    return bool(expected) and url_error(url) is None and _host(url) == expected


def evidence_link_ok(url: str) -> bool:
    return isinstance(url, str) and url.startswith("https://") and url_error(url, check_query=False) is None


def _md_url(url: str) -> str:
    """A validated url made safe for the (...) part of a markdown link."""
    return url.replace("(", "%28").replace(")", "%29")


def cost_badge(resource) -> str:
    """"Paid $59 per month with Coursera Plus": cost label, price and what the price covers (price_note)."""
    label = _COST_LABEL.get(resource.cost, "Paid")
    price = _price(resource.price_usd)
    note = " ".join(str(getattr(resource, "price_note", "") or "").split())
    if resource.cost in ("paid", "freemium") and price:
        text = md_escape(" ".join(part for part in (label, price, note) if part))
        return f":{'red' if resource.cost == 'paid' else 'gray'}-badge[{text}]"
    return f":{'red' if resource.cost == 'paid' else 'gray'}-badge[{label}]"


def hours_label(hours: int) -> str:
    return "1 hour" if hours == 1 else f"{hours} hours"


# --- controls and the principles ---

def render_controls(key: str) -> tuple[str, int]:
    """Two controls: "Show" (Free only / Free and paid) and "Time you have" (3 / 5 / 10 hours a week).

    The values live in session state under the given key, so they are kept per analysis. Returns
    (budget, hours_per_week) where budget is "free" or "any".
    """
    c1, c2 = st.columns(2)
    shown = c1.radio("Show", list(BUDGETS), horizontal=True, key=f"{key}_budget")
    hours = c2.radio(
        "Time you have", HOURS_OPTIONS, index=HOURS_OPTIONS.index(DEFAULT_HOURS), horizontal=True,
        format_func=lambda h: f"{h} hours a week", key=f"{key}_hours",
    )
    return BUDGETS.get(shown, "free"), int(hours)


def render_how_we_pick() -> None:
    """The seven principles, the no-sponsor line and the pointer to the Feedback page."""
    for i, text in enumerate(PRINCIPLES, 1):
        st.markdown(f"{i}. {text}")
    st.markdown(f"**{NOT_SPONSORED}**")
    st.page_link("views/feedback.py", label="Report a bad link or suggest one", icon=":material/chat:")


# --- events ---

def _log_click(fields: dict, card_key: str) -> None:
    flag = f"resource_clicked:{card_key}"
    if st.session_state.get(flag):
        return
    st.session_state[flag] = True
    log_event(events.RESOURCE_CLICKED, **fields)


def _log_rating(helpful: bool, fields: dict, card_key: str) -> None:
    flag = f"resource_rated:{card_key}"
    if st.session_state.get(flag) is not None:
        return  # one rating per card per session
    st.session_state[flag] = helpful
    log_event(events.RESOURCE_RATED, helpful=helpful, **fields)


def _log_missing(scope: str, skill: str, unit: str = "") -> None:
    """resource_missing, once per skill (or per gap, via unit) per analysis; the scope holds the analysis.

    skill is a skill id or the constant UNMATCHED. Gap text from a CV or a model is never passed in here.
    """
    logged = st.session_state.setdefault("resources_missing_logged", set())
    marker = f"{scope}|{skill}|{unit}"
    if marker in logged:
        return
    logged.add(marker)
    log_event(events.RESOURCE_MISSING, skill=skill)


# --- cards ---

def _reasons_line(reasons: Iterable[str]) -> str:
    return " · ".join(md_escape(r) for r in reasons)


def _render_evidence(resource) -> None:
    with st.expander("How we checked this"):
        for ev in resource.evidence:
            claim = md_escape(ev.claim)
            when = f" (seen {_month_year(ev.seen)})"
            if evidence_link_ok(ev.url):
                st.markdown(f"- {claim}: [source]({_md_url(ev.url)}){when}")
            else:
                st.markdown(f"- {claim}{when}")
        method = _EVIDENCE_METHOD.get(resource.verification, md_escape(resource.verification))
        st.markdown(f"Verified on {md_escape(resource.verified_at)}: {method}.")
        g = resource.rating
        if g is not None:
            value = f"{g.value:g}" if g.scale == 5 else f"{g.value:g} out of {g.scale:g}"
            line = f"Rating: {value} from {g.count:,} ratings (seen {_month_year(g.seen)})"
            if evidence_link_ok(g.source_url):
                line += f", [source]({_md_url(g.source_url)})"
            st.markdown(line + ".")


def render_pick(pick_: Pick, *, key: str, rank: int, skill: str) -> None:
    """One resource card. key must be unique on the page; rank is the 1-based position; skill is the skill id."""
    r = pick_.resource
    card_key = f"{_slug(key)}-{_slug(r.id)}"
    fields = {"skill": skill, "resource_id": r.id, "rank": rank}
    with st.container(border=True, key=f"res-{card_key}"):
        if resource_link_ok(r):
            st.link_button(
                md_escape(r.title), r.url, icon=":material/open_in_new:", key=f"{card_key}-open",
                on_click=_log_click, kwargs={"fields": fields, "card_key": card_key},
            )
        else:
            st.markdown(f"**{md_escape(r.title)}**")
            st.caption("The link is hidden because it did not pass our safety check. "
                       "You can report it on the Feedback page.")
        meta = [md_escape(r.provider), cost_badge(r), hours_label(r.hours), md_escape(r.level.capitalize())]
        if "uk" in r.languages:
            meta.append(":gray-badge[Ukrainian]")
        st.markdown(" · ".join(meta))
        if pick_.reasons:
            st.caption(_reasons_line(pick_.reasons))
        st.markdown(f"**Why we picked it:** {md_escape(r.why_picked)}")
        _render_evidence(r)

        rated = st.session_state.get(f"resource_rated:{card_key}")
        c1, c2, c3 = st.columns([1, 1, 3])
        for col, label, helpful in ((c1, "Helpful", True), (c2, "Not helpful", False)):
            col.button(
                label, key=f"{card_key}-{'yes' if helpful else 'no'}", disabled=rated is not None,
                on_click=_log_rating, args=(helpful, fields, card_key),
            )
        if rated is not None:
            c3.caption("Thanks")


# --- plan and the list ---

def _weeks(step) -> str:
    if step.week_start == step.week_end:
        return f"Week {step.week_start}"
    return f"Weeks {step.week_start} to {step.week_end}"


def render_plan(picks: list[Pick], hours_per_week: int) -> None:
    steps = build_plan(picks, hours_per_week)
    if not steps:
        return
    lines = []
    for step in steps:
        if step.is_final:
            lines.append(f"- **{_weeks(step)}:** {md_escape(step.title)}  \n  {md_escape(step.cv_line)}")
        else:
            lines.append(f"- **{_weeks(step)}:** {md_escape(step.title)}, {hours_label(step.hours)}")
    st.markdown(f"**Your plan** at {hours_per_week} hours a week\n\n" + "\n".join(lines))


def _gather(skill_ids: list[str], budget: str, language: str | None) -> list[tuple[str, Pick]]:
    """One skill: its best 3. Several skills: the best pick of each, at most two cards."""
    if len(skill_ids) == 1:
        return [(skill_ids[0], p) for p in pick(skill_ids[0], budget=budget, language=language, limit=MAX_CARDS)]
    out: list[tuple[str, Pick]] = []
    for sid in skill_ids:
        for p in pick(sid, budget=budget, language=language, limit=1):
            if all(p.resource.id != q.resource.id for _, q in out):
                out.append((sid, p))
        if len(out) >= 2:
            break
    return out


def _demand_caption(skill_ids: list[str], demand: list[dict] | None) -> str:
    if not demand:
        return ""
    total = demand[0].get("total_postings") or 0
    if not total:
        return ""
    by_skill = {str(d.get("skill", "")).lower(): d for d in demand}
    parts = []
    for sid in skill_ids:
        d = by_skill.get(sid.lower())
        if d and d.get("postings"):
            parts.append(f"{md_escape(sid)} is asked for in {round(100 * d['postings'] / total)}% of postings for your field")
    return "; ".join(parts)


def learn_count_caption(skill_id: str, label: str, budget: str, language: str | None) -> str:
    """The line above the cards on the Learn page, counted with the same filters pick() applies to the cards.

    "SQL: 1 shown for your settings; 2 more with Free and paid or in Ukrainian". With nothing shown it says why
    and which control to change. Returns "" when the catalog is broken (render_resources shows the soft message).
    """
    name = md_escape(label)
    try:
        shown = len(pick(skill_id, budget=budget, language=language, limit=ALL))
        with_paid = len(pick(skill_id, budget="any", language=language, limit=ALL)) if budget != "any" else shown
        wants_english = select_mod.normalize_language(language) == "en"
        with_uk = len(pick(skill_id, budget=budget, language="Ukrainian", limit=ALL)) if wants_english else shown
        with_both = (
            len(pick(skill_id, budget="any", language="Ukrainian", limit=ALL)) if wants_english and budget != "any"
            else max(with_paid, with_uk)
        )
    except CatalogError:
        return ""
    more_paid, more_uk, more_any = with_paid - shown, with_uk - shown, with_both - shown
    if shown == 0:
        if more_paid > 0:
            return (f"No free resources for this skill yet; {more_paid} paid option{' exists' if more_paid == 1 else 's exist'}: "
                    "switch Show to Free and paid.")
        if more_uk > 0:
            return (f"No English resources for this skill yet; {more_uk} in Ukrainian: "
                    "switch Language to Ukrainian.")
        return f"0 vetted resources for {name}"
    if more_any <= 0:
        if shown > MAX_CARDS:
            return f"{name}: the best {MAX_CARDS} of {shown} vetted resources"
        return f"{shown} vetted resource{'' if shown == 1 else 's'} for {name}"
    options = []
    if more_paid > 0:
        options.append("with Free and paid")
    if more_uk > 0:
        options.append("in Ukrainian")
    head = f"the best {MAX_CARDS} of {shown} shown" if shown > MAX_CARDS else f"{shown} shown"
    return f"{name}: {head} for your settings; {more_any} more {' or '.join(options)}"


def render_resources(
    skill_ids: list[str], *, key: str, feedback_language: str | None, budget: str, hours_per_week: int,
    demand: list[dict] | None = None, missing_text: str | None = None, scope: str | None = None,
) -> int:
    """Cards and a compact plan for the given skill ids. Returns the number of cards shown.

    With no skill ids or no vetted resource it says so and logs resource_missing once: the constant "unmatched"
    when a gap matched no skill (missing_text only tells that there was a gap; its text is never logged), or the
    skill ids when they have no resource. scope (default: key) is what "once" is counted per, e.g. the analysis.
    A broken catalog gives a soft message, never an exception.
    """
    ids = list(dict.fromkeys(s for s in skill_ids if isinstance(s, str) and s))[:3]
    if not ids:
        st.caption(NO_RESOURCE)
        if missing_text:
            _log_missing(scope or key, UNMATCHED, unit=key)
        return 0
    try:
        picks = _gather(ids, budget, feedback_language)
    except CatalogError:
        st.info(SOFT_ERROR, icon=":material/info:")
        return 0
    if not picks:
        st.caption(NO_RESOURCE)
        for sid in ids:
            _log_missing(scope or key, sid)
        return 0
    note = _demand_caption([s for s, _ in picks], demand)
    if note:
        st.caption(note)
    for rank, (sid, p) in enumerate(picks, 1):
        render_pick(p, key=key, rank=rank, skill=sid)
    render_plan([p for _, p in picks], hours_per_week)
    return len(picks)
