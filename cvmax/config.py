"""Налаштування, які можна змінити через змінні середовища."""

import os

# Який провайдер моделі: auto | gemini | claude.
# auto: Gemini, якщо є GEMINI_API_KEY, інакше Claude, якщо є ANTHROPIC_API_KEY, інакше демо.
PROVIDER = os.environ.get("CVMAX_PROVIDER", "auto")

# Claude
MODEL = os.environ.get("CVMAX_MODEL", "claude-opus-5-5")
# Якщо Claude відмовить через фільтр безпеки, API сам повторить запит на запасній моделі.
USE_FALLBACKS = os.environ.get("CVMAX_FALLBACKS", "1") == "1"

# Gemini: пробуємо моделі по черзі, якщо попередня перевантажена або вичерпала ліміт.
GEMINI_MODELS = [
    m.strip()
    for m in os.environ.get(
        "CVMAX_GEMINI_MODELS", "gemini-3.5-flash,gemini-flash-latest,gemini-3.1-flash-lite"
    ).split(",")
    if m.strip()
]

# Глибина міркувань моделі: low | medium | high.
# Аналіз CV важливий, тому high. Питання Grill me прості, тому low.
EFFORT_ANALYSIS = os.environ.get("CVMAX_EFFORT_ANALYSIS", "high")
EFFORT_GRILL = os.environ.get("CVMAX_EFFORT_GRILL", "low")

MAX_TOKENS = 16000

# Grill me: не більше стількох питань за одну сесію.
GRILL_MAX_QUESTIONS = 8

# Максимальний розмір файлу CV.
MAX_FILE_MB = 10
