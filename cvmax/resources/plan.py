"""Deterministic learning plan from a list of picks. No model call."""
from __future__ import annotations

import math

from .models import LEVELS, Pick, PlanStep

_LEVEL_ORDER = {name: i for i, name in enumerate(LEVELS)}


def build_plan(picks: list[Pick], hours_per_week: int) -> list[PlanStep]:
    """Orders picks by level, converts hours to weeks, and ends with a "Build and publish" step.

    Steps run one after another: a step with H hours at W hours per week takes ceil(H / W) weeks, at least 1.
    The final step has no resource; its title is "Build and publish: <output>" and cv_line names the CV line
    the user can add. It uses the output of the last hands-on pick (else the last pick) and takes one more week.
    Returns [] when there are no picks. A non-positive hours_per_week is treated as 1.
    """
    if not picks:
        return []
    per_week = max(int(hours_per_week), 1)
    # sorted() is stable, so picks of the same level keep their rank order.
    ordered = sorted(picks, key=lambda p: _LEVEL_ORDER.get(p.resource.level, len(_LEVEL_ORDER)))
    steps: list[PlanStep] = []
    week = 1
    for i, p in enumerate(ordered, start=1):
        r = p.resource
        weeks = max(1, math.ceil(r.hours / per_week))
        steps.append(PlanStep(
            order=i, title=f"{r.title} ({r.provider})", resource=r, hours=r.hours,
            week_start=week, week_end=week + weeks - 1,
        ))
        week += weeks
    hands_on = [p for p in ordered if p.resource.hands_on]
    last = (hands_on or ordered)[-1].resource
    steps.append(PlanStep(
        order=len(steps) + 1, title=f"Build and publish: {last.output}", resource=None, hours=0,
        week_start=week, week_end=week, is_final=True,
        cv_line=f"Add to your CV: {last.output}",
    ))
    return steps
