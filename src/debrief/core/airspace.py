"""Airspace from OpenAIR files.

Airspace is read from a file on disk rather than fetched, because the file is
the thing you want pinned: a flight is debriefed against the airspace as it was,
and a live fetch silently re-dates old flights. openAIP publishes OpenAIR
exports (updated weekly) that drop straight in.

``aerofiles.openair`` does the lexing; this module turns its geometry elements
into closed rings in ``[longitude, latitude]`` order and parses the altitude
limits into something comparable with a flight's altitude band.

Not for navigation. openAIP's data is explicitly uncertified, and the vertical
limits here are approximations (see :func:`parse_altitude`). This is a
post-flight analysis overlay.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path

from aerofiles.openair import Reader
from pyproj import Geod

logger = logging.getLogger(__name__)

GEOD = Geod(ellps="WGS84")

METRES_PER_NM = 1852.0
FEET_PER_METRE = 3.280839895

# OpenAIR radii are in nautical miles unless a file says otherwise; aerofiles
# passes the number through unconverted.
Ring = list[list[float]]


@dataclass(frozen=True)
class Altitude:
    """One vertical limit of an airspace."""

    feet: float | None  # None means unlimited
    datum: str  # "msl" | "agl" | "unlimited"
    text: str  # what the file actually said, for display

    @property
    def is_ground(self) -> bool:
        return self.datum == "agl" and self.feet == 0

    @property
    def is_unlimited(self) -> bool:
        return self.datum == "unlimited"


@dataclass(frozen=True)
class Airspace:
    name: str
    airspace_class: str | None
    airspace_type: str | None
    floor: Altitude
    ceiling: Altitude
    rings: tuple[Ring, ...]

    @property
    def label(self) -> str:
        bits = [self.name]
        if self.airspace_class:
            bits.append(f"class {self.airspace_class}")
        return " · ".join(bits)

    def intersects_band(self, low_ft: float, high_ft: float) -> bool:
        """Whether this airspace overlaps a flight's altitude band.

        AGL limits are treated as reaching the ground, because resolving them
        properly needs a terrain model this project does not carry yet. That
        errs toward showing an airspace rather than hiding one, which is the
        safe direction for a filter whose job is "what was near me".
        """
        floor = 0.0 if self.floor.datum == "agl" else (self.floor.feet or 0.0)
        ceiling = math.inf if self.ceiling.is_unlimited else (self.ceiling.feet or 0.0)
        if self.ceiling.datum == "agl":
            ceiling = math.inf
        return floor <= high_ft and ceiling >= low_ft


_FLIGHT_LEVEL = re.compile(r"^FL\s*(\d+(?:\.\d+)?)$", re.I)
_WITH_UNIT = re.compile(r"^(\d+(?:\.\d+)?)\s*(FT|F|M)?\s*(MSL|AMSL|ALT|AGL|GND|SFC|ASFC)?$", re.I)


def parse_altitude(text: str | None) -> Altitude:
    """Parse an OpenAIR altitude limit.

    Handles ``GND``/``SFC``, ``UNLIM``, flight levels and foot/metre values with
    an optional MSL or AGL datum. Flight levels are converted at 100 ft each,
    which is pressure altitude rather than true altitude — close enough to place
    an airspace against a barogram, not close enough to clear one.
    """
    raw = (text or "").strip()
    if not raw:
        return Altitude(None, "unlimited", "?")

    upper = raw.upper().replace("  ", " ")

    if upper in {"GND", "SFC", "ASFC", "0", "0FT", "0 FT"}:
        return Altitude(0.0, "agl", raw)
    if upper.startswith("UNL"):
        return Altitude(None, "unlimited", raw)

    flight_level = _FLIGHT_LEVEL.match(upper)
    if flight_level:
        return Altitude(float(flight_level.group(1)) * 100.0, "msl", raw)

    match = _WITH_UNIT.match(upper)
    if match:
        value = float(match.group(1))
        if (match.group(2) or "FT").upper() == "M":
            value *= FEET_PER_METRE
        datum = (match.group(3) or "MSL").upper()
        return Altitude(value, "agl" if datum in {"AGL", "GND", "SFC", "ASFC"} else "msl", raw)

    logger.warning("unrecognised airspace altitude %r; treating as unlimited", raw)
    return Altitude(None, "unlimited", raw)


def _circle_ring(center: tuple[float, float], radius_m: float, points: int = 72) -> Ring:
    """A geodesic circle as a closed ring in [lon, lat] order."""
    lat, lon = center
    ring = []
    for step in range(points + 1):
        p_lon, p_lat, _ = GEOD.fwd(lon, lat, 360.0 * step / points, radius_m)
        ring.append([p_lon, p_lat])
    return ring


def _arc_points(
    center: tuple[float, float],
    radius_m: float,
    start_deg: float,
    end_deg: float,
    clockwise: bool,
    step_deg: float = 5.0,
) -> Ring:
    lat, lon = center
    sweep = (end_deg - start_deg) % 360.0 if clockwise else (start_deg - end_deg) % 360.0
    if sweep == 0.0:
        sweep = 360.0
    steps = max(int(sweep / step_deg), 2)
    ring = []
    for index in range(steps + 1):
        travelled = sweep * index / steps
        bearing = start_deg + travelled if clockwise else start_deg - travelled
        p_lon, p_lat, _ = GEOD.fwd(lon, lat, bearing % 360.0, radius_m)
        ring.append([p_lon, p_lat])
    return ring


def _elements_to_rings(elements: list[dict]) -> list[Ring]:
    """Walk OpenAIR geometry elements into closed rings.

    A record is usually one ring built from points and arcs. A ``DC`` circle is
    self-contained and becomes a ring of its own.
    """
    rings: list[Ring] = []
    current: Ring = []

    for element in elements:
        kind = element.get("type")

        if kind == "point":
            lat, lon = element["location"]
            current.append([lon, lat])

        elif kind == "circle":
            if current:
                rings.append(current)
                current = []
            rings.append(_circle_ring(element["center"], float(element["radius"]) * METRES_PER_NM))

        elif kind == "arc":
            center = element["center"]
            clockwise = bool(element.get("clockwise", True))
            if "radius" in element:  # DA: radius plus start/end bearings
                radius_m = float(element["radius"]) * METRES_PER_NM
                start = float(element["start"])
                end = float(element["end"])
            else:  # DB: start and end given as points on the arc
                start_lat, start_lon = element["start"]
                end_lat, end_lon = element["end"]
                start, _, radius_m = GEOD.inv(center[1], center[0], start_lon, start_lat)
                end, _, _ = GEOD.inv(center[1], center[0], end_lon, end_lat)
                start %= 360.0
                end %= 360.0
            current.extend(_arc_points(center, radius_m, start, end, clockwise))

        elif kind == "airway":  # DY — a corridor, not an area; no sane polygon
            continue

    if current:
        rings.append(current)

    closed = []
    for ring in rings:
        if len(ring) < 3:
            continue
        if ring[0] != ring[-1]:
            ring = [*ring, ring[0]]
        closed.append(ring)
    return closed


def load_openair(path: str | Path, strict: bool = False) -> list[Airspace]:
    """Read an OpenAIR file into :class:`Airspace` objects.

    Malformed records are skipped with a warning unless ``strict``: a national
    airspace file is thousands of records and one bad block should not cost you
    the rest of the country.
    """
    path = Path(path)
    airspaces: list[Airspace] = []

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="latin1")

    for record, error in Reader(text.splitlines(keepends=True)):
        if error:
            if strict:
                raise error
            logger.warning("skipping airspace record: %s", error)
            continue
        if record is None or record.get("type") != "airspace":
            continue

        rings = _elements_to_rings(record.get("elements", []))
        if not rings:
            continue

        airspaces.append(
            Airspace(
                name=record.get("name") or "(unnamed)",
                airspace_class=record.get("class"),
                airspace_type=record.get("airspace_type"),
                floor=parse_altitude(record.get("floor")),
                ceiling=parse_altitude(record.get("ceiling")),
                rings=tuple(rings),
            )
        )

    return airspaces
