"""Domain model.

The model is a small star schema. ``Flight`` is the fact: one trace, flown by one
pilot, over one task. ``Pilot`` and ``TaskDef`` are peer dimensions, each with a
stable key, so a set of flights can be grouped either way:

    same task, many pilots   -> group by ``Flight.task_key``
    one pilot, many days     -> group by ``Flight.pilot_key``

Keeping both keys on the fact is what lets the two comparison axes coexist without
one being privileged. Nothing here knows about IGC files, HTTP, or Streamlit.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field
from typing import Any

# A "fix" is kept as a plain dict for the whole pipeline because that is what
# aerofiles produces and what opensoar consumes. Converting to a richer type and
# back on every opensoar call would cost more than it buys. Required keys:
#   datetime (tz-aware), lat, lon, gps_alt, pressure_alt
Fix = dict[str, Any]


def _key(*parts: object) -> str:
    """Short, stable, content-derived identifier."""
    joined = "|".join(str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class Pilot:
    """Dimension: who flew. ``competition_id`` is the glider's contest number (CN)."""

    name: str | None = None
    competition_id: str | None = None
    glider_model: str | None = None
    glider_registration: str | None = None

    @property
    def key(self) -> str:
        # Name is the only cross-competition stable attribute; CN is reassigned
        # between contests and gliders get sold, so neither can anchor identity.
        return _key("pilot", (self.name or "").strip().lower())

    @property
    def label(self) -> str:
        if self.name and self.competition_id:
            return f"{self.competition_id} — {self.name}"
        return self.name or self.competition_id or "unknown pilot"


@dataclass(frozen=True)
class TaskPoint:
    """One turnpoint of a declared task, with its observation zone.

    The observation-zone fields mirror opensoar's ``Waypoint`` so a task can be
    converted back without loss. Sector geometry decides whether a fix counts as a
    turnpoint rounding, so dropping it would silently change leg times.
    """

    name: str
    latitude: float
    longitude: float
    r_max: float | None = None
    angle_max: float | None = None
    r_min: float | None = None
    angle_min: float | None = None
    is_line: bool = False
    sector_orientation: str = "symmetrical"
    distance_correction: str | None = None
    orientation_angle: float | None = None


@dataclass(frozen=True)
class TaskDef:
    """Dimension: what was flown.

    The key is derived from turnpoint geometry rounded to ~1 m, so every pilot on a
    competition day resolves to the same task even though each IGC file carries its
    own copy of the declaration.
    """

    points: tuple[TaskPoint, ...]
    task_type: str = "race"  # "race" | "aat"
    t_min: dt.timedelta | None = None
    start_opening: dt.datetime | None = None
    timezone_hours: int | None = None
    # True when the observation zones are defaults rather than the ones the
    # pilot declared. Standard IGC C records carry only coordinates and names,
    # so a task recovered from them has assumed sector sizes — which changes
    # turnpoint rounding times, and therefore leg times and speeds. Anything
    # showing scored numbers has to say so.
    geometry_assumed: bool = False

    @property
    def key(self) -> str:
        geometry = [
            (p.name.strip().lower(), round(p.latitude, 5), round(p.longitude, 5), p.r_max)
            for p in self.points
        ]
        return _key("task", self.task_type, geometry)

    @property
    def n_legs(self) -> int:
        return max(len(self.points) - 1, 0)

    def leg_label(self, leg: int) -> str:
        return f"{self.points[leg].name} → {self.points[leg + 1].name}"

    @property
    def label(self) -> str:
        return " / ".join(p.name for p in self.points)


@dataclass(frozen=True)
class FlightSource:
    """Provenance. The raw file is the durable asset; everything else is derived."""

    kind: str  # "local" | "soaringspot" | ...
    reference: str  # file path, or the URL the IGC came from
    imported_at: dt.datetime | None = None


@dataclass
class Flight:
    """Fact: one trace, one pilot, one task."""

    trace: list[Fix]
    pilot: Pilot
    date: dt.date
    task: TaskDef | None = None
    source: FlightSource | None = None
    competition: CompetitionDayRef | None = None
    ranking: int | str | None = None
    headers: dict[str, Any] = field(default_factory=dict)

    @property
    def pilot_key(self) -> str:
        return self.pilot.key

    @property
    def task_key(self) -> str | None:
        return self.task.key if self.task else None

    @property
    def flight_key(self) -> str:
        return _key("flight", self.pilot_key, self.date.isoformat(), self.task_key)

    @property
    def label(self) -> str:
        return f"{self.pilot.label} — {self.date:%Y-%m-%d}"


@dataclass(frozen=True)
class CompetitionDayRef:
    """Where a flight came from in competition terms. Optional: a free-flight has none."""

    competition: str
    plane_class: str | None = None
    date: dt.date | None = None
