"""Stable score: a weighted sum of the rubric criteria instead of the model's "feeling".

The model rates each rubric criterion on a 1..5 scale. We compute the total ourselves:
the criterion weight from rubrics/weights.json, a small contribution from the model's overall score
and a penalty for the deterministic checks (cvmax.checks).
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from . import config
from .schemas import Analysis

log = logging.getLogger(__name__)

# The weights file sits next to the rubrics: its hash is part of the knowledge version (learning/version.py).
WEIGHTS_PATH = Path(__file__).parent / "rubrics" / "weights.json"

# If fewer criteria are matched, the weighted sum cannot be trusted: we take the model's score.
MIN_MATCHED = 4

# Fallback values for when config does not have the new fields yet.
_DEFAULT_MODEL_WEIGHT = 0.3
_DEFAULT_PENALTY_CAP = 15

# Penalty points per check finding (matches cvmax.checks.penalty).
_SEVERITY_POINTS = {"high": 4, "medium": 2, "low": 1}

_MAX_ALIAS_HOPS = 5  # how many times we follow a string alias, so as not to loop forever
_MAX_NAME_CHARS = 200  # the criterion name is cut to this before matching

# Default weights if weights.json is missing or corrupted.
_BUILTIN_DEFAULT: dict[str, int] = {
    "Target fit": 25,
    "Impact bullets": 20,
    "Evidence and numbers": 15,
    "Structure and scannability": 10,
    "Length and density": 10,
    "ATS-friendliness": 10,
    "Language quality": 10,
}


@dataclass
class ScoreDetail:
    score: int  # total 0..100
    criteria_score: int  # weighted sum of the criteria 0..100 (equals model_score when matched < MIN_MATCHED)
    model_score: int  # overall_score given by the model
    penalty: int  # from the checks
    matched: int  # how many of the model's criteria were matched to weights
    items: list[tuple[str, int, int]]  # (criterion under its name in weights.json, weight, score 1..5)


# --- weights ---


def _clean_weights(raw: Any) -> dict[str, int]:
    """Keeps only "name -> positive integer" pairs; silently drops everything else."""
    if not isinstance(raw, dict):
        return {}
    return {
        k: v
        for k, v in raw.items()
        if isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) and v > 0
    }


def _read_weights_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("weights file unavailable: %s (%s)", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def load_weights(program: str, path: Path | None = None) -> dict[str, int]:
    """Criterion weights for a program.

    A string instead of an object means "same as that program" (a chain no longer than _MAX_ALIAS_HOPS).
    An unknown program, a corrupted entry or a missing file give the "default" weights.
    Returns a new dict: the caller may remove used keys from it.
    """
    data = _read_weights_file(Path(path) if path is not None else WEIGHTS_PATH)
    entry: Any = data.get(program) if isinstance(program, str) else None
    for _ in range(_MAX_ALIAS_HOPS):
        if not isinstance(entry, str):
            break
        entry = data.get(entry)
    weights = _clean_weights(entry)
    if not weights:
        weights = _clean_weights(data.get("default"))
    return weights or dict(_BUILTIN_DEFAULT)


# --- matching a criterion name to a weights key ---


def _words(text: str) -> list[str]:
    """Lower case, letters and spaces only (every other character becomes a space), split into words."""
    chars = [ch.lower() if ch.isalpha() else " " for ch in str(text)[:_MAX_NAME_CHARS]]
    return "".join(chars).split()


def match_criterion(name: str, weights: dict[str, int]) -> str | None:
    """The key in weights that corresponds to the criterion name given by the model; None if there is no match.

    Order: an exact match after normalisation; a key all of whose words are among the words of the name
    (if several, the longest is taken, the first on a tie); a key with the same first word.
    The caller removes a used key from weights, so it cannot be chosen twice.
    """
    if not isinstance(weights, dict):
        return None
    name_words = _words(name)
    if not name_words:
        return None
    keys = [(k, _words(k)) for k in weights if isinstance(k, str)]
    keys = [(k, w) for k, w in keys if w]

    for key, key_words in keys:
        if key_words == name_words:
            return key

    name_set = set(name_words)
    inside = [(k, w) for k, w in keys if set(w) <= name_set]
    if inside:
        return max(inside, key=lambda kw: len(kw[1]))[0]  # max returns the first of the equals

    for key, key_words in keys:
        if key_words[0] == name_words[0]:
            return key
    return None


# --- penalty and config ---


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _model_weight() -> Fraction:
    """Share of the model's overall score in the total (0..1), as an exact fraction without float error."""
    try:
        raw = float(getattr(config, "SCORE_MODEL_WEIGHT", _DEFAULT_MODEL_WEIGHT))
    except (TypeError, ValueError):
        raw = _DEFAULT_MODEL_WEIGHT
    if not math.isfinite(raw):
        raw = _DEFAULT_MODEL_WEIGHT
    return Fraction(min(max(raw, 0.0), 1.0)).limit_denominator(10000)


def _penalty_cap() -> int:
    try:
        cap = int(getattr(config, "PENALTY_CAP", _DEFAULT_PENALTY_CAP))
    except (TypeError, ValueError):
        cap = _DEFAULT_PENALTY_CAP
    return max(cap, 0)


def _local_penalty(checks: Any, cap: int) -> int:
    """Fallback penalty calculation when cvmax.checks.penalty is unavailable: high=4, medium=2, low=1."""
    findings = getattr(checks, "findings", None)
    if not isinstance(findings, (list, tuple)):
        return 0
    total = sum(_SEVERITY_POINTS.get(getattr(f, "severity", ""), 0) for f in findings)
    return min(total, cap)


def _checks_penalty(checks: Any) -> int:
    """Penalty for the checks: cvmax.checks.penalty if the module already exists, otherwise a local calculation."""
    if checks is None:
        return 0
    cap = _penalty_cap()
    penalty_fn = None
    try:
        # Lazy import: the checks module is written in parallel, so we do not make a hard dependency.
        from . import checks as checks_module

        penalty_fn = getattr(checks_module, "penalty", None)
    except Exception as exc:  # a missing or broken module must not break the score
        log.debug("cvmax.checks unavailable, using local penalty: %s", exc)
    if callable(penalty_fn):
        try:
            return _clamp(int(penalty_fn(checks)), 0, cap)
        except Exception as exc:
            log.warning("checks.penalty failed, using local penalty: %s", exc)
    return _local_penalty(checks, cap)


# --- total ---


def compute_score(analysis: Analysis, checks: Any | None, program: str) -> ScoreDetail:
    """Final score 0..100. Does not modify analysis; the result is deterministic.

    criteria_score = round(100 * Σ w_i * (s_i - 1) / 4 / Σ w_i) over the matched criteria;
    score = clamp(round((1 - W) * criteria_score + W * model_score) - penalty, 0, 100),
    where W = config.SCORE_MODEL_WEIGHT. If fewer than MIN_MATCHED criteria are matched,
    score = clamp(model_score - penalty), and criteria_score equals model_score.
    Halves round up, the calculation is exact (integers and fractions).
    """
    model_score = _clamp(int(analysis.overall_score), 0, 100)
    penalty = _checks_penalty(checks)

    remaining = load_weights(program)
    items: list[tuple[str, int, int]] = []
    for criterion in analysis.scores:
        key = match_criterion(criterion.criterion, remaining)
        if key is None:
            continue
        weight = remaining.pop(key)  # each key is used once
        items.append((key, weight, _clamp(int(criterion.score), 1, 5)))
    matched = len(items)

    if matched < MIN_MATCHED:
        return ScoreDetail(
            score=_clamp(model_score - penalty, 0, 100),
            criteria_score=model_score,
            model_score=model_score,
            penalty=penalty,
            matched=matched,
            items=items,
        )

    total_weight = sum(w for _, w, _ in items)
    numerator = 100 * sum(w * (s - 1) for _, w, s in items)
    denominator = 4 * total_weight
    criteria_score = (2 * numerator + denominator) // (2 * denominator)  # halves round up

    model_weight = _model_weight()
    blended = (1 - model_weight) * criteria_score + model_weight * model_score
    score = _clamp(math.floor(blended + Fraction(1, 2)) - penalty, 0, 100)
    return ScoreDetail(
        score=score,
        criteria_score=criteria_score,
        model_score=model_score,
        penalty=penalty,
        matched=matched,
        items=items,
    )
