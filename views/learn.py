"""Learn: vetted, hand-checked resources for one skill at a time. Open to everyone, no sign-in needed."""

import streamlit as st

from cvmax.resources import CatalogError, load_skills
from ui.resources import (
    SOFT_ERROR, get_catalog, learn_count_caption, render_controls, render_how_we_pick, render_resources,
)

DEFAULT_SKILL = "sql"
LANGUAGES = ["English", "Ukrainian"]  # the values cvmax.profile.FEEDBACK_LANGUAGES maps to

st.title("Learn")
st.markdown(
    '<p class="cvx-page-lead">Pick a skill and see the few resources we would send a friend to, with the reason '
    "we picked each one and what we saw when we checked it. Free first, and every plan ends with something you can "
    "put on your CV.</p>",
    unsafe_allow_html=True,
)

try:
    skills = load_skills()
    catalog = get_catalog()
except CatalogError:
    st.info(SOFT_ERROR, icon=":material/info:")
    skills, catalog = [], []

if skills:
    labels = {s.id: s.label for s in skills}  # the vocabulary lists skills in cluster order
    counts: dict[str, int] = {}
    for r in catalog:
        for sid in r.skills:
            counts[sid] = counts.get(sid, 0) + 1
    ids = list(labels)
    skill = st.selectbox(
        "Skill", ids, index=ids.index(DEFAULT_SKILL) if DEFAULT_SKILL in ids else 0,
        format_func=lambda sid: labels[sid] + (f" ({counts[sid]})" if counts.get(sid) else ""),
        key="learn_skill", help="Type to search. The number is all vetted resources for the skill, before the Show and Language settings.",
    )
    budget, hours = render_controls("learn")
    # English by default: Ukrainian-only resources are shown only to readers who choose Ukrainian.
    profile = st.session_state.get("profile")
    default_language = getattr(profile, "feedback_language", None)
    language = st.radio(
        "Language", LANGUAGES, index=LANGUAGES.index(default_language) if default_language in LANGUAGES else 0,
        horizontal=True, key="learn_language", help="Ukrainian shows Ukrainian-language courses first.",
    )
    # The count uses the same filters as the cards (language, budget), so the number and the cards agree.
    count_line = learn_count_caption(skill, labels[skill], budget, language)
    if count_line:
        st.caption(count_line)
    render_resources(
        [skill], key=f"learn-{skill}", feedback_language=language,
        budget=budget, hours_per_week=hours,
    )

st.subheader("How we pick resources")
render_how_we_pick()
