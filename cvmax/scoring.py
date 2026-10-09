"""Стабільний бал: зважена сума критеріїв рубрики замість «відчуття» моделі.

Модель оцінює кожен критерій рубрики за шкалою 1..5. Підсумок рахуємо ми самі:
вага критерію з rubrics/weights.json, невеликий внесок загальної оцінки моделі
і штраф за детерміновані перевірки (cvmax.checks).
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

# Файл ваг лежить поруч із рубриками: його хеш входить у версію знань (learning/version.py).
WEIGHTS_PATH = Path(__file__).parent / "rubrics" / "weights.json"

# Якщо зіставлено менше критеріїв, зваженій сумі довіряти не можна: беремо бал моделі.
MIN_MATCHED = 4

# Резервні значення на випадок, коли config ще не має нових полів.
_DEFAULT_MODEL_WEIGHT = 0.3
_DEFAULT_PENALTY_CAP = 15

# Бали штрафу за знахідки перевірок (збігається з cvmax.checks.penalty).
_SEVERITY_POINTS = {"high": 4, "medium": 2, "low": 1}

_MAX_ALIAS_HOPS = 5  # скільки разів йдемо за рядком-псевдонімом, щоб не зациклитись
_MAX_NAME_CHARS = 200  # назву критерію обрізаємо перед зіставленням

# Ваги за замовчуванням, якщо weights.json зник або зіпсований.
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
    score: int  # підсумок 0..100
    criteria_score: int  # зважена сума критеріїв 0..100 (при matched < MIN_MATCHED дорівнює model_score)
    model_score: int  # overall_score, який дала модель
    penalty: int  # з перевірок
    matched: int  # скільки критеріїв моделі зіставлено з вагами
    items: list[tuple[str, int, int]]  # (критерій за назвою з weights.json, вага, бал 1..5)


# --- ваги ---


def _clean_weights(raw: Any) -> dict[str, int]:
    """Лишає лише пари «назва -> додатне ціле»; усе інше мовчки відкидає."""
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
    """Ваги критеріїв для програми.

    Рядок замість об'єкта означає «як у цієї програми» (ланцюжок не довший за _MAX_ALIAS_HOPS).
    Невідома програма, зіпсований запис або відсутній файл дають ваги "default".
    Повертає новий словник: викликач може вилучати з нього використані ключі.
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


# --- зіставлення назви критерію з ключем ваг ---


def _words(text: str) -> list[str]:
    """Нижній регістр, лише літери й пробіли (решта символів стає пробілом), слова окремо."""
    chars = [ch.lower() if ch.isalpha() else " " for ch in str(text)[:_MAX_NAME_CHARS]]
    return "".join(chars).split()


def match_criterion(name: str, weights: dict[str, int]) -> str | None:
    """Ключ із weights, що відповідає назві критерію від моделі; None, якщо збігу немає.

    Порядок: точний збіг після нормалізації; ключ, усі слова якого є серед слів назви
    (при кількох береться найдовший, при рівності перший); ключ із тим самим першим словом.
    Ключ, який уже використано, викликач вилучає з weights, тому двічі він не вибереться.
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
        return max(inside, key=lambda kw: len(kw[1]))[0]  # max віддає перший із рівних

    for key, key_words in keys:
        if key_words[0] == name_words[0]:
            return key
    return None


# --- штраф і конфіг ---


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _model_weight() -> Fraction:
    """Частка загальної оцінки моделі в підсумку (0..1), точним дробом без похибок float."""
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
    """Запасний підрахунок штрафу, якщо cvmax.checks.penalty недоступна: high=4, medium=2, low=1."""
    findings = getattr(checks, "findings", None)
    if not isinstance(findings, (list, tuple)):
        return 0
    total = sum(_SEVERITY_POINTS.get(getattr(f, "severity", ""), 0) for f in findings)
    return min(total, cap)


def _checks_penalty(checks: Any) -> int:
    """Штраф за перевірки: cvmax.checks.penalty, якщо модуль уже є, інакше локальний підрахунок."""
    if checks is None:
        return 0
    cap = _penalty_cap()
    penalty_fn = None
    try:
        # Лінивий імпорт: модуль checks пишеться паралельно, жорсткої залежності не робимо.
        from . import checks as checks_module

        penalty_fn = getattr(checks_module, "penalty", None)
    except Exception as exc:  # відсутній або зламаний модуль не повинен ламати бал
        log.debug("cvmax.checks unavailable, using local penalty: %s", exc)
    if callable(penalty_fn):
        try:
            return _clamp(int(penalty_fn(checks)), 0, cap)
        except Exception as exc:
            log.warning("checks.penalty failed, using local penalty: %s", exc)
    return _local_penalty(checks, cap)


# --- підсумок ---


def compute_score(analysis: Analysis, checks: Any | None, program: str) -> ScoreDetail:
    """Підсумковий бал 0..100. Не змінює analysis; результат детермінований.

    criteria_score = round(100 * Σ w_i * (s_i - 1) / 4 / Σ w_i) по зіставлених критеріях;
    score = clamp(round((1 - W) * criteria_score + W * model_score) - penalty, 0, 100),
    де W = config.SCORE_MODEL_WEIGHT. Якщо зіставлено менше MIN_MATCHED критеріїв,
    score = clamp(model_score - penalty), а criteria_score дорівнює model_score.
    Округлення половин вгору, обчислення точні (цілі числа й дроби).
    """
    model_score = _clamp(int(analysis.overall_score), 0, 100)
    penalty = _checks_penalty(checks)

    remaining = load_weights(program)
    items: list[tuple[str, int, int]] = []
    for criterion in analysis.scores:
        key = match_criterion(criterion.criterion, remaining)
        if key is None:
            continue
        weight = remaining.pop(key)  # кожен ключ використовується один раз
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
    criteria_score = (2 * numerator + denominator) // (2 * denominator)  # половини вгору

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
