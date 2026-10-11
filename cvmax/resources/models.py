"""Data models for vetted learning resources. Plain dataclasses, no network, no model calls."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Allowed values for the enumerated fields. validate() in catalog.py enforces them.
KINDS = ("course", "tutorial", "video-course", "practice", "book", "certificate", "exam", "channel")
LEVELS = ("beginner", "intermediate", "advanced")
COSTS = ("free", "free-audit", "freemium", "paid")
VERIFICATIONS = ("fetched", "search-snippet")
STATUSES = ("active", "needs_check", "dead")
CLUSTERS = ("data", "software", "business", "language_career", "law_psychology")

MAX_WHY_PICKED = 200
MAX_PRICE_NOTE = 60


class ResourceFormatError(ValueError):
    """A resource dict is structurally wrong (missing field, wrong container type)."""


def _need(d: Any, key: str, where: str) -> Any:
    if not isinstance(d, dict):
        raise ResourceFormatError(f"{where} must be an object")
    if key not in d:
        raise ResourceFormatError(f"{where}: missing field {key!r}")
    return d[key]


@dataclass
class Evidence:
    """One thing a human saw that justifies the pick."""
    claim: str
    url: str
    seen: str  # ISO date

    @classmethod
    def from_dict(cls, d: Any, where: str = "evidence") -> "Evidence":
        return cls(claim=_need(d, "claim", where), url=_need(d, "url", where), seen=_need(d, "seen", where))

    def to_dict(self) -> dict:
        return {"claim": self.claim, "url": self.url, "seen": self.seen}


@dataclass
class Rating:
    """Public rating of the resource: value out of scale, from count reviews."""
    value: float
    scale: float
    count: int
    source_url: str
    seen: str  # ISO date

    @classmethod
    def from_dict(cls, d: Any, where: str = "rating") -> "Rating":
        return cls(
            value=_need(d, "value", where), scale=_need(d, "scale", where), count=_need(d, "count", where),
            source_url=_need(d, "source_url", where), seen=_need(d, "seen", where),
        )

    def to_dict(self) -> dict:
        return {"value": self.value, "scale": self.scale, "count": self.count,
                "source_url": self.source_url, "seen": self.seen}


@dataclass
class Resource:
    """One catalog entry. Field names and meaning follow docs/plan-resources-and-free-launch.md, Part E."""
    id: str
    title: str
    provider: str
    url: str
    kind: str
    skills: list[str]
    level: str
    cost: str
    price_usd: float | None
    hours: int
    languages: list[str]
    hands_on: bool
    output: str
    why_picked: str
    evidence: list[Evidence]
    rating: Rating | None
    updated_year: int | None
    verified_at: str  # ISO date
    verification: str
    status: str
    price_note: str = ""  # what price_usd covers, e.g. "per month with Coursera Plus"; "" for free entries

    FIELDS = (
        "id", "title", "provider", "url", "kind", "skills", "level", "cost", "price_usd", "hours",
        "languages", "hands_on", "output", "why_picked", "evidence", "rating", "updated_year",
        "verified_at", "verification", "status", "price_note",
    )
    OPTIONAL_FIELDS = ("price_note",)  # may be missing from a data file; the default is used

    @classmethod
    def from_dict(cls, d: Any) -> "Resource":
        """Builds a Resource from a catalog dict. Raises ResourceFormatError for a missing field or bad container.

        Scalar types and enum values are not checked here; validate() reports those.
        """
        where = f"resource {d.get('id')!r}" if isinstance(d, dict) and isinstance(d.get("id"), str) else "resource"
        values = {name: _need(d, name, where) for name in cls.FIELDS if name not in cls.OPTIONAL_FIELDS}
        if isinstance(d, dict) and "price_note" in d:
            values["price_note"] = d["price_note"]
        ev = values["evidence"]
        if not isinstance(ev, list):
            raise ResourceFormatError(f"{where}: evidence must be a list")
        values["evidence"] = [Evidence.from_dict(e, f"{where}: evidence[{i}]") for i, e in enumerate(ev)]
        rating = values["rating"]
        values["rating"] = None if rating is None else Rating.from_dict(rating, f"{where}: rating")
        return cls(**values)

    def to_dict(self) -> dict:
        out = {name: getattr(self, name) for name in self.FIELDS}
        out["evidence"] = [e.to_dict() for e in self.evidence]
        out["rating"] = None if self.rating is None else self.rating.to_dict()
        return out


@dataclass
class Skill:
    """One entry of the skill vocabulary (skills.json)."""
    id: str
    label: str
    aliases: list[str]
    programs: list[str]


@dataclass(frozen=True)
class Votes:
    """Aggregated user votes for one resource, read back from the weekly learning export."""
    helpful: int
    not_helpful: int

    @property
    def total(self) -> int:
        return self.helpful + self.not_helpful

    @property
    def share(self) -> float:
        """Helpful share in 0..1; 0.0 when there are no votes."""
        return self.helpful / self.total if self.total else 0.0


@dataclass
class Pick:
    """A selected resource with its points and the human-readable reasons shown verbatim in the app."""
    resource: Resource
    points: int
    reasons: list[str] = field(default_factory=list)


@dataclass
class PlanStep:
    """One step of a learning plan. The last step has resource=None and carries the CV line."""
    order: int
    title: str
    resource: Resource | None
    hours: int
    week_start: int
    week_end: int
    is_final: bool = False
    cv_line: str = ""
