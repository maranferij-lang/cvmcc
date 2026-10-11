"""Dictionary of learning events. A payload must not contain CV text."""
from __future__ import annotations

import json

ANALYSIS_DONE = "analysis_done"
ANALYSIS_RATED = "analysis_rated"
EDITS_DECIDED = "edits_decided"
GRILL_TURN = "grill_turn"
EXPORT = "export"
RESCAN = "rescan"
JOBS_SHOWN = "jobs_shown"
JOB_APPLIED = "job_applied"
OUTCOME = "outcome"
MATCH_DONE = "match_done"
SHARE_CARD = "share_card"
RESOURCE_CLICKED = "resource_clicked"
RESOURCE_RATED = "resource_rated"
RESOURCE_MISSING = "resource_missing"

KINDS = frozenset({
    ANALYSIS_DONE, ANALYSIS_RATED, EDITS_DECIDED, GRILL_TURN, EXPORT,
    RESCAN, JOBS_SHOWN, JOB_APPLIED, OUTCOME, MATCH_DONE, SHARE_CARD,
    RESOURCE_CLICKED, RESOURCE_RATED, RESOURCE_MISSING,
})

ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    ANALYSIS_DONE: frozenset({
        "analysis_id", "task", "variant", "program", "region", "level", "score",
        "knowledge_version", "lessons_version", "previous_score", "n_edits", "n_gaps", "clarity",
        # Components of the stable score and a summary of the automatic checks and the edit verifier.
        "model_score", "criteria_score", "penalty", "n_checks", "verify_dropped", "verify_fixed",
    }),
    ANALYSIS_RATED: frozenset({"analysis_id", "rating"}),
    EDITS_DECIDED: frozenset({"analysis_id", "accepted", "total"}),
    GRILL_TURN: frozenset({"analysis_id", "kind", "answered"}),
    EXPORT: frozenset({"analysis_id", "format"}),
    RESCAN: frozenset({"analysis_id", "previous_analysis_id", "previous_score", "score", "delta"}),
    JOBS_SHOWN: frozenset({"role_tag", "region", "n", "sources"}),
    JOB_APPLIED: frozenset({"role_tag", "region", "source", "fit"}),
    OUTCOME: frozenset({"result_id", "analysis_id", "answer"}),
    # Review against a posting: counts only, never the posting or CV text. source: "form" | "jobs".
    MATCH_DONE: frozenset({"analysis_id", "score", "must_total", "must_covered", "nice_total", "source"}),
    # A downloaded share card: before is None when the card shows a single score.
    SHARE_CARD: frozenset({"analysis_id", "before", "after", "size"}),
    # Learning resources: catalog ids and skill ids only, never free text from a CV.
    RESOURCE_CLICKED: frozenset({"skill", "resource_id", "rank"}),
    RESOURCE_RATED: frozenset({"skill", "resource_id", "rank", "helpful"}),
    RESOURCE_MISSING: frozenset({"skill"}),
}

MAX_PAYLOAD_CHARS = 4000
MAX_STR_CHARS = 200


def _clean(value):
    """Returns the cleaned value, or _DROP if the type is not allowed."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_STR_CHARS]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return [v[:MAX_STR_CHARS] for v in value]
    return _DROP


_DROP = object()


def make_payload(kind: str, /, **fields) -> dict:
    """Builds a safe payload: only allowed fields and simple types."""
    if kind not in KINDS:
        raise ValueError(f"unknown event kind: {kind!r}")
    allowed = ALLOWED_FIELDS[kind]
    payload = {}
    for name, value in fields.items():
        if name not in allowed:
            continue
        cleaned = _clean(value)
        if cleaned is not _DROP:
            payload[name] = cleaned
    if len(json.dumps(payload, ensure_ascii=False)) > MAX_PAYLOAD_CHARS:
        raise ValueError("event payload too long")
    return payload
