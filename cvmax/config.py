"""Settings that can be changed through environment variables."""

import os

# Which model provider: auto | gemini | claude.
# auto: Gemini if GEMINI_API_KEY is set, otherwise Claude if ANTHROPIC_API_KEY is set, otherwise demo.
PROVIDER = os.environ.get("CVMAX_PROVIDER", "auto")

# Claude
MODEL = os.environ.get("CVMAX_MODEL", "claude-opus-5-5")
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
# The site-wide limits are chosen for free Gemini, so the site does not hit the model limits.
LIMITS = {
    "analysis": (5, 60),
    "career": (3, 40),
    "grill": (3, 30),
    "builder": (2, 20),
    "build": (4, 30),
    "export": (5, 30),
    "feedback": (10, 500),
    "jobs": (10, 300),
}

# Job search: overall timeout of one request to a source, in seconds.
JOBS_TIMEOUT_S = 8.0
# How many job postings after the preliminary ranking we pass to the model and show.
JOBS_MAX_RESULTS = 40
# Lifetime of the source response cache, in seconds (30 minutes).
JOBS_CACHE_TTL_S = 1800
# How many requests to sources we run in parallel.
JOBS_WORKERS = 6
