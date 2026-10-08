"""Thompson sampling над варіантами промпту з автовідключенням невдах. Чисті функції."""
from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

VARIANTS_DIR = Path(__file__).resolve().parents[1] / "variants"
BASELINE_ID = "v1-baseline"
EXPLORE_FLOOR = 0.10
MIN_TRIALS_PROGRAM = 10
MIN_TRIALS_DISABLE = 30
DISABLE_MARGIN = 0.15


@dataclass(frozen=True)
class Variant:
    id: str
    active: bool
    text: str
    programs: tuple[str, ...] = ()  # порожньо = для всіх сфер


def load_variants(task: str, directory: Path | None = None) -> list[Variant]:
    """Читає <directory>/<task>.json; при помилці повертає []."""
    path = Path(directory or VARIANTS_DIR) / f"{task}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [
            Variant(id=str(v["id"]), active=bool(v.get("active", True)), text=str(v.get("text", "")),
                    programs=tuple(str(x) for x in (v.get("programs") or [])))
            for v in data["variants"]
        ]
    except FileNotFoundError:
        log.warning("variants file missing: %s", path)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        log.warning("variants file invalid: %s (%s)", path, exc)
    return []


def variant_text(task: str, variant_id: str, directory: Path | None = None) -> str:
    for v in load_variants(task, directory):
        if v.id == variant_id:
            return v.text
    return ""


def summarise(stats: list[dict], program: str) -> dict[str, tuple[int, int]]:
    """Для кожного варіанта: рядок програми, якщо випробувань досить, інакше сума по всіх."""
    totals: dict[str, list[int]] = {}
    own: dict[str, tuple[int, int]] = {}
    for row in stats:
        vid = row["variant"]
        s, f = int(row.get("successes", 0)), int(row.get("failures", 0))
        t = totals.setdefault(vid, [0, 0])
        t[0] += s
        t[1] += f
        if row.get("program") == program:
            prev = own.get(vid, (0, 0))
            own[vid] = (prev[0] + s, prev[1] + f)
    out = {}
    for vid, (s, f) in totals.items():
        ps, pf = own.get(vid, (0, 0))
        out[vid] = (ps, pf) if ps + pf >= MIN_TRIALS_PROGRAM else (s, f)
    return out


def disabled_variants(summary: dict) -> set[str]:
    """Варіанти, що помітно гірші за найкращий; останній варіант не вимикається."""
    means = {
        vid: s / (s + f)
        for vid, (s, f) in summary.items()
        if s + f >= MIN_TRIALS_DISABLE
    }
    if len(means) < 2:
        return set()
    best = max(means.values())
    return {vid for vid, m in means.items() if m < best - DISABLE_MARGIN}


def choose_variant(task: str, program: str, stats: list[dict],
                   rng: random.Random | None = None,
                   directory: Path | None = None) -> str:
    rng = rng or random.Random()
    variants = load_variants(task, directory)
    summary = summarise(stats, program)
    # Спершу кандидати (активні й доступні для програми), лише потім правило вимкнення:
    # варіанти, що не можуть обслуговувати цю програму, не впливають на «найкращого».
    cands = [v.id for v in variants if v.active and (not v.programs or program in v.programs)]
    off = disabled_variants({k: summary[k] for k in cands if k in summary})
    eligible = [v for v in cands if v not in off]
    if not eligible:
        return BASELINE_ID if any(v.id == BASELINE_ID for v in variants) else ""
    if rng.random() < EXPLORE_FLOOR:
        return rng.choice(eligible)
    best_id, best_draw = eligible[0], -1.0
    for vid in eligible:
        s, f = summary.get(vid, (0, 0))
        draw = rng.betavariate(1 + s, 1 + f)
        if draw > best_draw:
            best_id, best_draw = vid, draw
    return best_id
