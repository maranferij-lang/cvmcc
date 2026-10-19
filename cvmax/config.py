"""Settings that can be changed through environment variables."""

import logging
import os

# Which model provider: auto | gemini | claude.
# auto: Gemini if GEMINI_API_KEY is set, otherwise Claude if ANTHROPIC_API_KEY is set, otherwise demo.
PROVIDER = os.environ.get("CVMAX_PROVIDER") or "auto"  # an empty value (an unset CI variable) means auto

# Claude
# The model for heavy tasks (CV review, edits from Q&A, career directions, CV builder, weekly lessons) is Sonnet:
# about half the price of Opus at the same quality bar for structured CV work. Light tasks (effort "low":
# Q&A questions, job ranking, the edit verifier, skill extraction) run on Haiku, about 20 times cheaper.
# Set CVMAX_MODEL or CVMAX_MODEL_LIGHT to change either; run evals/run_evals.py before switching.
MODEL = os.environ.get("CVMAX_MODEL") or "claude-sonnet-5-5"
MODEL_LIGHT = os.environ.get("CVMAX_MODEL_LIGHT") or "claude-haiku-5-5"
# The weekly judge (cvmax.judge) reviews samples with its own prompt; same default, separate switch.
MODEL_JUDGE = os.environ.get("CVMAX_MODEL_JUDGE") or "claude-sonnet-5-5"
# Models that accept the server-side refusal fallback. Haiku 5.5 has no such fallback and would reject the field.
FALLBACK_MODELS = frozenset({"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"})
# If Claude refuses because of the safety filter, the API itself retries the request on a fallback model.
USE_FALLBACKS = os.environ.get("CVMAX_FALLBACKS", "1") == "1"

# Gemini: on the free tier each model has its own daily request limit
# (for the full Flash models it is only 20 per day per project). So we try the models one by one:
# the daily limits of different models add up.
def _models(env: str, default: str) -> list[str]:
    return [m.strip() for m in os.environ.get(env, default).split(",") if m.strip()]


# Heavy tasks (review, "Where to apply", CV building): the best models first.
GEMINI_MODELS_HEAVY = _models(
    "CVMAX_GEMINI_MODELS",
    "gemini-3.5-flash,gemini-3.6-flash,gemini-3.8-flash,gemini-3.7-flash,gemini-3-flash-preview,"
    "gemini-flash-latest,gemini-3.5-flash-lite,gemini-3.1-flash-lite",
)
# Light tasks (Grill me questions): Lite models first, to save the limit of the full ones.
GEMINI_MODELS_LIGHT = _models(
    "CVMAX_GEMINI_MODELS_LIGHT",
    "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-flash-lite-latest,gemini-3.6-flash,gemini-3-flash-preview",
)

# Model reasoning depth: low | medium | high.
# The CV review matters, so high. The Grill me questions are simple, so low.
EFFORT_ANALYSIS = os.environ.get("CVMAX_EFFORT_ANALYSIS", "high")
EFFORT_GRILL = os.environ.get("CVMAX_EFFORT_GRILL", "low")

MAX_TOKENS = 16000
# Claude answers stream, so the output cap can be high: a full line review of a long real CV with thinking at effort
# "high" did not fit in 16k and got cut off. Only the tokens actually generated are billed.
MAX_TOKENS_STREAM = 64000

# Deterministic CV checks (cvmax.checks) and the edit verifier (cvmax.verify): 1 = on, 0 = off.
CHECKS_ENABLED = os.environ.get("CVMAX_CHECKS", "1") == "1"
VERIFY_ENABLED = os.environ.get("CVMAX_VERIFY", "1") == "1"

# Stable score (cvmax.scoring): the share of the final score taken from the model's overall score (the rest is the weighted criteria).
SCORE_MODEL_WEIGHT = 0.3
# The largest penalty to the score for automatic-check findings, in points.
PENALTY_CAP = 15

# Grill me: no more than this many questions per session.
GRILL_MAX_QUESTIONS = 8

# Maximum size of a CV file.
MAX_FILE_MB = 10

# Daily usage limits: (per user, for the whole site). None = no limit.
# The site-wide caps keep the Claude bill near $15 a day at the estimated $0.10-0.15 per review (owner decision,
# 2026-10-19): about 80 reviews plus the smaller tools.
LIMITS = {
    "analysis": (2, 80),
    "career": (1, 10),
    "grill": (2, 60),
    "builder": (1, 10),
    "build": (2, 20),
    "export": (3, 80),
    "feedback": (10, 500),
    "jobs": (5, 150),
    "match": (2, 20),
    "upload": (10, 400),
    "grill_retry": (6, 120),  # retries of a failed Q&A step; a first try is covered by "grill"
}


def apply_limit_overrides(limits: dict, raw: str | None) -> list[str]:
    """Applies `CVMAX_LIMITS` to `limits` in place and returns the entries it ignored.

    Format: `analysis=10:200,jobs=20:600` (per-user:site-wide per day). A side may be `none` for no limit.
    Kind names are not case sensitive. Unknown kinds, malformed numbers and negative values are ignored, never raised: a typo in a
    deployment variable must not take the site down.
    """
    ignored: list[str] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        kind, _, value = part.partition("=")
        kind = kind.strip().lower()
        user_s, sep, site_s = value.partition(":")
        try:
            if kind not in limits or not sep:
                raise ValueError(part)
            parsed = []
            for side in (user_s, site_s):
                side = side.strip().lower()
                number = None if side == "none" else int(side)
                if number is not None and not 0 <= number <= 1_000_000:
                    raise ValueError(part)
                parsed.append(number)
            limits[kind] = (parsed[0], parsed[1])
        except ValueError:
            ignored.append(part[:60])
    return ignored


# Raise limits after enabling paid Gemini, without a code change: CVMAX_LIMITS="analysis=10:300,jobs=20:600".
_IGNORED_LIMITS = apply_limit_overrides(LIMITS, os.environ.get("CVMAX_LIMITS"))
for _entry in _IGNORED_LIMITS:  # only the entry text (already cut to 60 characters), nothing else from the environment
    logging.getLogger("cvmax").warning("Ignored invalid CVMAX_LIMITS entry: %r", _entry)

# Job search: overall timeout of one request to a source, in seconds.
JOBS_TIMEOUT_S = 8.0
# How many job postings after the preliminary ranking we pass to the model and show.
JOBS_MAX_RESULTS = 40
# Lifetime of the source response cache, in seconds (30 minutes).
JOBS_CACHE_TTL_S = 1800
# How many requests to sources we run in parallel.
JOBS_WORKERS = 6

# Paywall prices shown in the app, in whole US dollars (docs/plan-paywall.md). The real charge is the Paddle price;
# these are only labels. Format: `full=15,hunt=29`. Unknown plans and malformed numbers are ignored.
PRICE_DEFAULTS = {"full": 15, "hunt": 29}
PLAN_KEYS = ("full", "hunt")


def parse_prices(raw: str | None) -> dict[str, int]:
    """Applies `CVMAX_PRICES` over the defaults. Never raises: a typo must not take the site down."""
    prices = dict(PRICE_DEFAULTS)
    for part in (raw or "").split(","):
        plan, _, value = part.partition("=")
        plan, value = plan.strip().lower(), value.strip()
        if plan in PLAN_KEYS and value.isdigit() and 0 < int(value) <= 100_000:
            prices[plan] = int(value)
    return prices


PRICES = parse_prices(os.environ.get("CVMAX_PRICES"))


def paddle_settings() -> dict[str, str]:
    """Paddle variables, read from the environment at call time (secrets: never log the result).

    Keys: api_key, env ("sandbox" | "live", default sandbox), price_full, price_hunt. Missing values are "".
    """
    env = (os.environ.get("PADDLE_ENV") or "sandbox").strip().lower()
    return {
        "api_key": (os.environ.get("PADDLE_API_KEY") or "").strip(),
        "env": env if env in ("sandbox", "live") else "sandbox",
        "price_full": (os.environ.get("PADDLE_PRICE_FULL") or "").strip(),
        "price_hunt": (os.environ.get("PADDLE_PRICE_HUNT") or "").strip(),
    }
