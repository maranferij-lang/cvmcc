"""Налаштування, які можна змінити через змінні середовища."""

import os

# Який провайдер моделі: auto | gemini | claude.
# auto: Gemini, якщо є GEMINI_API_KEY, інакше Claude, якщо є ANTHROPIC_API_KEY, інакше демо.
PROVIDER = os.environ.get("CVMAX_PROVIDER", "auto")

# Claude
MODEL = os.environ.get("CVMAX_MODEL", "claude-opus-5-5")
# Якщо Claude відмовить через фільтр безпеки, API сам повторить запит на запасній моделі.
USE_FALLBACKS = os.environ.get("CVMAX_FALLBACKS", "1") == "1"

# Gemini: на безплатному тарифі кожна модель має окремий денний ліміт запитів
# (у повних Flash-моделей це лише 20 на день на проєкт). Тому пробуємо моделі по черзі:
# денні ліміти різних моделей додаються.
def _models(env: str, default: str) -> list[str]:
    return [m.strip() for m in os.environ.get(env, default).split(",") if m.strip()]


# Важкі задачі (аналіз, «Куди податись», збирання CV): спершу найкращі моделі.
GEMINI_MODELS_HEAVY = _models(
    "CVMAX_GEMINI_MODELS",
    "gemini-3.5-flash,gemini-3.6-flash,gemini-3.8-flash,gemini-3.7-flash,gemini-3-flash-preview,"
    "gemini-flash-latest,gemini-3.5-flash-lite,gemini-3.1-flash-lite",
)
# Легкі задачі (питання Grill me): спершу Lite-моделі, щоб берегти ліміт повних.
GEMINI_MODELS_LIGHT = _models(
    "CVMAX_GEMINI_MODELS_LIGHT",
    "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-flash-lite-latest,gemini-3.6-flash,gemini-3-flash-preview",
)

# Глибина міркувань моделі: low | medium | high.
# Аналіз CV важливий, тому high. Питання Grill me прості, тому low.
EFFORT_ANALYSIS = os.environ.get("CVMAX_EFFORT_ANALYSIS", "high")
EFFORT_GRILL = os.environ.get("CVMAX_EFFORT_GRILL", "low")

MAX_TOKENS = 16000

# Grill me: не більше стількох питань за одну сесію.
GRILL_MAX_QUESTIONS = 8

# Максимальний розмір файлу CV.
MAX_FILE_MB = 10

# Денні ліміти використання: (на одного юзера, на весь сайт). None = без обмеження.
# Загальні ліміти підібрані під безплатний Gemini, щоб сайт не «впирався» в ліміт моделей.
LIMITS = {
    "analysis": (5, 60),
    "career": (3, 40),
    "grill": (3, 30),
    "builder": (2, 20),
    "build": (4, 30),
    "export": (5, 30),
}
