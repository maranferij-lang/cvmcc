"""Налаштування, які можна змінити через змінні середовища."""

import os

# Модель для всіх викликів. Змінити можна без правок коду.
MODEL = os.environ.get("CVMAX_MODEL", "claude-opus-5-5")

# Глибина міркувань моделі: low | medium | high | xhigh | max.
# Аналіз CV важливий, тому high. Питання Grill me прості, тому low.
EFFORT_ANALYSIS = os.environ.get("CVMAX_EFFORT_ANALYSIS", "high")
EFFORT_GRILL = os.environ.get("CVMAX_EFFORT_GRILL", "low")

# Якщо модель відмовить через фільтр безпеки, API сам повторить запит
# на запасній моделі. Для CV це майже ніколи не спрацьовує, але це безплатна страховка.
USE_FALLBACKS = os.environ.get("CVMAX_FALLBACKS", "1") == "1"

MAX_TOKENS = 16000

# Grill me: не більше стількох питань за одну сесію.
GRILL_MAX_QUESTIONS = 8

# Максимальний розмір файлу CV.
MAX_FILE_MB = 10
