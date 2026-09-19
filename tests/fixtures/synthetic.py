"""Generate a synthetic SoaringSpot-style IGC file for a flown race task.

Real competition IGC files cannot be committed here (they are other people's data,
and this environment cannot reach soaringspot.com anyway), so the test suite flies
its own glider. The generated file is deliberately faithful in the ways the
analysis depends on:

* 35-character B records, so ``aerofiles`` parses them;
* ``LCU::C`` / ``LSEEYOU OZ`` declaration lines in SoaringSpot's dialect, so
  ``opensoar`` reconstructs the task from the file alone;
* thermals flown as actual circles, so the PySoar detector — which triggers on
  >225 deg of consistent turn and exits on >1 km of straight flight — classifies
  them as thermals rather than smoothing them away.

The last point matters: a fixture that climbs by simply incrementing altitude would
parse fine and silently produce zero thermals.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from pyproj import Geod

GEOD = Geod(ellps="WGS84")


@dataclass(frozen=True)
class TaskPointSpec:
    name: str
    lat: float
    lon: float
    radius: float = 3000.0


# A ~330 km triangle out of Klix (Germany) — a real Central European contest site.
DEFAULT_TASK = (
    TaskPointSpec("KLIX", 51.2917, 14.5167, radius=5000.0),
    TaskPointSpec("JUETERBOG", 52.1000, 13.0500, radius=3000.0),
    TaskPointSpec("RIESA", 51.3000, 13.3000, radius=3000.0),
    TaskPointSpec("KLIXFIN", 51.2917, 14.5167, radius=3000.0),
)

# The same triangle with a turnpoint shortly before home, so the last leg is a
# short run-in flown without a climb. Real contest tasks look like this far more
# often than DEFAULT_TASK does, and it is the shape that exposed a final-glide
# bug: the last thermal sits on an earlier leg.
SHORT_FINAL_LEG_TASK = (
    TaskPointSpec("KLIX", 51.2917, 14.5167, radius=5000.0),
    TaskPointSpec("JUETERBOG", 52.1000, 13.0500, radius=3000.0),
    TaskPointSpec("RIESA", 51.3000, 13.3000, radius=3000.0),
    TaskPointSpec("NEARHOME", 51.2917, 14.2600, radius=3000.0),
    TaskPointSpec("KLIXFIN", 51.2917, 14.5167, radius=3000.0),
)


def _dm(value: float, deg_width: int) -> tuple[int, int, int]:
    """Split a signed decimal degree into degrees, minutes, thousandths of minutes."""
    magnitude = abs(value)
    degrees = int(magnitude)
    minutes_float = (magnitude - degrees) * 60.0
    minutes = int(minutes_float)
    thousandths = int(round((minutes_float - minutes) * 1000.0))
    if thousandths == 1000:  # rounding carry
        thousandths = 0
        minutes += 1
    if minutes == 60:
        minutes = 0
        degrees += 1
    assert degrees < 10**deg_width
    return degrees, minutes, thousandths


def format_lat(lat: float) -> str:
    d, m, t = _dm(lat, 2)
    return f"{d:02d}{m:02d}{t:03d}{'N' if lat >= 0 else 'S'}"


def format_lon(lon: float) -> str:
    d, m, t = _dm(lon, 3)
    return f"{d:03d}{m:02d}{t:03d}{'E' if lon >= 0 else 'W'}"


def b_record(when: dt.datetime, lat: float, lon: float, alt: int) -> str:
    """A 35-character B record. Pressure and GPS altitude are offset like a real logger."""
    return f"B{when:%H%M%S}{format_lat(lat)}{format_lon(lon)}A{max(alt - 30, 0):05d}{max(alt, 0):05d}"


class _Glider:
    """Minimal point-mass glider: it cruises toward a target and circles in thermals."""

    def __init__(self, lat: float, lon: float, alt: float, start: dt.datetime, step: int = 2):
        self.lat = lat
        self.lon = lon
        self.alt = alt
        self.time = start
        self.step = step
        self.fixes: list[tuple[dt.datetime, float, float, float]] = []
        self._record()

    def _record(self) -> None:
        self.fixes.append((self.time, self.lat, self.lon, self.alt))

    def _advance(self, distance: float, bearing: float, climb: float) -> None:
        self.lon, self.lat, _ = GEOD.fwd(self.lon, self.lat, bearing, distance)
        self.alt += climb * self.step
        self.time += dt.timedelta(seconds=self.step)
        self._record()

    def bearing_to(self, lat: float, lon: float) -> float:
        azimuth, _, _ = GEOD.inv(self.lon, self.lat, lon, lat)
        return azimuth

    def distance_to(self, lat: float, lon: float) -> float:
        _, _, distance = GEOD.inv(self.lon, self.lat, lon, lat)
        return distance

    def cruise_toward(self, lat: float, lon: float, speed: float, sink: float) -> None:
        """One cruise step of ``step`` seconds toward the target."""
        self._advance(speed * self.step, self.bearing_to(lat, lon), -abs(sink))

    def thermal(
        self,
        climb: float,
        to_altitude: float,
        period: float = 24.0,
        radius: float = 130.0,
        drift_bearing: float = 90.0,
        drift_speed: float = 4.0,
    ) -> None:
        """Circle until ``to_altitude``, drifting downwind like a real thermal.

        The glider is placed on a circle of ``radius`` whose centre drifts, so the
        track bearing rotates at 360/period deg/s — comfortably above the detector's
        4 deg/s entry threshold.
        """
        # Put the circle centre abeam of the current position.
        entry_bearing = self.bearing_to(*_offset(self.lat, self.lon, 0.0, 1.0))
        c_lon, c_lat, _ = GEOD.fwd(self.lon, self.lat, entry_bearing + 90.0, radius)
        angle = (entry_bearing + 270.0) % 360.0  # bearing from centre back to glider
        omega = 360.0 / period

        while self.alt < to_altitude:
            angle = (angle + omega * self.step) % 360.0
            c_lon, c_lat, _ = GEOD.fwd(c_lon, c_lat, drift_bearing, drift_speed * self.step)
            self.lon, self.lat, _ = GEOD.fwd(c_lon, c_lat, angle, radius)
            self.alt += climb * self.step
            self.time += dt.timedelta(seconds=self.step)
            self._record()


def _offset(lat: float, lon: float, bearing: float, distance: float) -> tuple[float, float]:
    lon2, lat2, _ = GEOD.fwd(lon, lat, bearing, distance)
    return lat2, lon2


def _distance_home(glider: _Glider, task: tuple[TaskPointSpec, ...], next_index: int) -> float:
    """Track distance still to fly: to the next turnpoint, then around the rest."""
    remaining = glider.distance_to(task[next_index].lat, task[next_index].lon)
    for first, second in zip(task[next_index:-1], task[next_index + 1 :], strict=True):
        remaining += GEOD.inv(first.lon, first.lat, second.lon, second.lat)[2]
    return remaining


def fly_task(
    task: tuple[TaskPointSpec, ...] = DEFAULT_TASK,
    date: dt.date = dt.date(2024, 6, 15),
    start_time: dt.time = dt.time(11, 30),
    cruise_speed: float = 33.0,
    cruise_sink: float = 1.15,
    climb_rate: float = 2.2,
    band_low: float = 1100.0,
    band_high: float = 2000.0,
    finish_margin: float = 200.0,
    step: int = 2,
) -> list[tuple[dt.datetime, float, float, float]]:
    """Fly the task and return the raw fixes.

    Climb rate decays slightly along the flight so the metrics have something to
    show rather than a flat line.
    """
    utc = dt.UTC
    begin = dt.datetime.combine(date, start_time, tzinfo=utc)
    start_point = task[0]
    glide_ratio = cruise_speed / cruise_sink

    # Begin inside the start cylinder, pointing away from the first turnpoint, so
    # the trace genuinely crosses the start rather than beginning on it.
    entry_bearing = GEOD.inv(start_point.lon, start_point.lat, task[1].lon, task[1].lat)[0]
    lat0, lon0 = _offset(start_point.lat, start_point.lon, (entry_bearing + 180.0) % 360.0, 2500.0)

    glider = _Glider(lat0, lon0, band_high - 150.0, begin, step=step)

    # A pre-start climb, so the flight does not begin mid-air at task speed.
    glider.thermal(climb_rate, band_high)

    for index, target in enumerate(task[1:], start=1):
        # Climb decays ~8% per leg: later legs are weaker, as an afternoon usually is.
        leg_climb = climb_rate * (1.0 - 0.08 * (index - 1))
        leg_band_high = band_high - 60.0 * (index - 1)
        guard = 0
        # Turnpoints get rounded closely - a flight that only grazes a 3 km
        # sector would miss a 500 m one entirely - but the task ends the moment
        # the finish ring is crossed, which on a big ring is kilometres short of
        # the finish point. That asymmetry is what exposes an assumed finish
        # sector that is smaller than the declared one.
        is_finish = index == len(task) - 1
        arrival = target.radius * 0.95 if is_finish else min(target.radius * 0.6, 250.0)
        while glider.distance_to(target.lat, target.lon) > arrival:
            guard += 1
            if guard > 20000:  # pragma: no cover - would mean the sim diverged
                raise RuntimeError(f"failed to reach {target.name}")
            # Stop climbing once home is within glide: a pilot on final glide does
            # not take another thermal. This is what leaves the last leg of a
            # short run-in with no climb at all, which is the normal shape of a
            # real task and the case a naive final-glide calculation misses.
            on_final_glide = _distance_home(glider, task, index) <= (glider.alt - finish_margin) * glide_ratio
            if glider.alt <= band_low and not on_final_glide:
                glider.thermal(leg_climb, leg_band_high)
            glider.cruise_toward(target.lat, target.lon, cruise_speed, cruise_sink)

    return glider.fixes


def build_igc(
    task: tuple[TaskPointSpec, ...] = DEFAULT_TASK,
    date: dt.date = dt.date(2024, 6, 15),
    pilot: str = "Anna Beispiel",
    glider_model: str = "Ventus 3",
    glider_id: str = "D-1234",
    competition_id: str = "7L",
    competition_class: str = "18m",
    timezone_hours: int = 2,
    dialect: str = "soaringspot",
    **fly_kwargs,
) -> str:
    """Build a complete IGC file as text.

    ``dialect`` selects how the task is declared:

    ``soaringspot``
        ``LCU::C`` / ``LSEEYOU OZ`` comment lines, which carry full sector
        geometry. This is what SoaringSpot and SeeYou write.
    ``igc``
        standard ``C`` records only — coordinates and names, no sectors. This
        is how most loggers declare a task, and a file like this used to read
        as having no task at all.
    ``both``
        C records and comment lines, as the real competition files carry.
    """
    if dialect not in {"soaringspot", "igc", "both"}:
        raise ValueError(f"unknown dialect {dialect!r}")
    fixes = fly_task(task=task, date=date, **fly_kwargs)

    lines: list[str] = [
        "AXCS synthetic debrief fixture",
        f"HFDTE{date:%d%m%y}",
        f"HFPLTPILOTINCHARGE:{pilot}",
        f"HFGTYGLIDERTYPE:{glider_model}",
        f"HFGIDGLIDERID:{glider_id}",
        f"HFCIDCOMPETITIONID:{competition_id}",
        f"HFCCLCOMPETITIONCLASS:{competition_class}",
        # SoaringSpot restates the header fields as LCU comments; opensoar reads
        # these, not the H records.
        f"LCU::HPPLTPILOT:{pilot}",
        f"LCU::HPGTYGLIDERTYPE:{glider_model}",
        f"LCU::HPCIDCOMPETITIONID:{competition_id}",
        f"LCU::HPCCLCOMPETITIONCLASS:{competition_class}",
        f"LCU::HPTZNTIMEZONE:{timezone_hours}",
    ]

    # Standard IGC declaration: a header record, then takeoff, the task points,
    # and landing. Unspecified takeoff/landing points are written as 0/0.
    if dialect in {"igc", "both"}:
        lines.append(f"C{date:%d%m%y}120000{date:%d%m%y}0000{len(task) - 2:02d}")
        lines.append("C0000000N00000000E")
        for point in task:
            lines.append(f"C{format_lat(point.lat)}{format_lon(point.lon)}{point.name}")
        lines.append("C0000000N00000000E")

    # SoaringSpot's own copy. opensoar reads lcu_lines[2:-1] as the task, so the
    # two leading records (C header, takeoff) and the trailing landing record
    # are required padding, not decoration.
    if dialect in {"soaringspot", "both"}:
        lines.append(f"LCU::C{date:%d%m%y}000000{date:%d%m%y}0001{len(task) - 2:02d}")
        lines.append("LCU::C0000000N00000000ETAKEOFF")
        for point in task:
            lines.append(f"LCU::C{format_lat(point.lat)}{format_lon(point.lon)}{point.name}")
        lines.append("LCU::C0000000N00000000ELANDING")

        # Observation zones. OZ=-1 is the start, then 0..n-2 for the rest; Style
        # is ignored for start and finish by opensoar, which always treats them
        # as 'next' and 'previous'.
        for index, point in enumerate(task):
            oz = index - 1
            style = 2 if index == 0 else (3 if index == len(task) - 1 else 1)
            lines.append(f"LSEEYOU OZ={oz},Style={style},R1={int(point.radius)}m,A1=180")

    for when, lat, lon, alt in fixes:
        lines.append(b_record(when, lat, lon, int(round(alt))))

    return "\n".join(lines) + "\n"


def write_igc(path, **kwargs) -> str:
    text = build_igc(**kwargs)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return text


if __name__ == "__main__":  # pragma: no cover
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "synthetic.igc"
    write_igc(target)
    print(f"wrote {target}")
