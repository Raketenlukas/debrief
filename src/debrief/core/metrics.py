"""Flight analysis: what the pilot actually did, measured against the declared task.

Four metric families, each computed per leg and for the flight as a whole:

climb
    thermal count, average climb rate, time spent circling
cruise & glide
    inter-thermal speed, achieved glide ratio, detour over the task line
task execution
    per-leg speed, start height and time, final glide
energy
    working altitude band, height gained and lost

Everything is derived from the trace and the task; nothing needs the scorer's
result sheet. That keeps the numbers comparable between a competition flight and
a training flight over the same task.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from opensoar.task.trip import Trip
from opensoar.thermals.flight_phases import FlightPhases
from opensoar.utilities.helper_functions import (
    altitude_gain_and_loss,
    calculate_distance_bearing,
    total_distance_travelled,
)

from debrief.core.igc import to_opensoar_task
from debrief.core.models import Fix, Flight, TaskDef

# opensoar's own default. GPS altitude is the usual choice for height-above-sea
# analysis; pressure altitude is the one the scorer uses for start-height limits,
# which is why the source is a parameter rather than a constant.
DEFAULT_GPS_ALTITUDE = True


def fix_altitude(fix: Fix, gps: bool = DEFAULT_GPS_ALTITUDE) -> float:
    """Altitude of a fix in metres, from the configured altitude source."""
    return float(fix["gps_alt"] if gps else fix["pressure_alt"])


# Short internal alias — this is used on every fix in some paths.
_alt = fix_altitude


def _seconds(start: dt.datetime, end: dt.datetime) -> float:
    return (end - start).total_seconds()


def _safe_div(numerator: float, denominator: float) -> float | None:
    """Ratios here are all physical quantities that are meaningless at zero."""
    if denominator is None or abs(denominator) < 1e-9:
        return None
    return numerator / denominator


@dataclass(frozen=True)
class Thermal:
    """One climb, as segmented by the PySoar detector."""

    leg: int | None
    start_time: dt.datetime
    end_time: dt.datetime
    entry_altitude: float
    exit_altitude: float

    @property
    def duration_s(self) -> float:
        return _seconds(self.start_time, self.end_time)

    @property
    def height_gain(self) -> float:
        return self.exit_altitude - self.entry_altitude

    @property
    def average_climb_ms(self) -> float | None:
        return _safe_div(self.height_gain, self.duration_s)


@dataclass(frozen=True)
class LegMetrics:
    """All four metric families, restricted to one leg of the task."""

    index: int
    label: str
    completed: bool

    # task execution
    task_distance_km: float
    start_time: dt.datetime
    end_time: dt.datetime
    duration_s: float
    speed_kmh: float | None

    # climb
    thermals: tuple[Thermal, ...]
    circling_s: float
    thermal_height_gain: float

    # cruise & glide
    distance_flown_km: float
    cruise_s: float
    cruise_height_loss: float
    cruise_distance_km: float
    cruise_straight_km: float

    # energy
    altitude_start: float
    altitude_end: float
    altitude_min: float
    altitude_max: float
    altitude_mean: float
    height_gain: float
    height_loss: float

    @property
    def thermal_count(self) -> int:
        return len(self.thermals)

    @property
    def average_climb_ms(self) -> float | None:
        return _safe_div(self.thermal_height_gain, self.circling_s)

    @property
    def percent_circling(self) -> float | None:
        ratio = _safe_div(self.circling_s, self.duration_s)
        return None if ratio is None else ratio * 100.0

    @property
    def cruise_speed_kmh(self) -> float | None:
        speed = _safe_div(self.cruise_distance_km * 1000.0, self.cruise_s)
        return None if speed is None else speed * 3.6

    @property
    def glide_ratio(self) -> float | None:
        """Achieved L/D over the cruise phases — the number that pays for the day."""
        return _safe_div(self.cruise_distance_km * 1000.0, self.cruise_height_loss)

    @property
    def detour_percent(self) -> float | None:
        """How much further than a straight line the pilot cruised.

        Measured cruise-track against cruise-straight-line, *not* against the task
        distance. Comparing total track to task distance would fold circling into
        the number (a normal flight lands around +50%, which says nothing about
        course-keeping) and would also be distorted by thermal drift, which moves
        the glider along the task for free.
        """
        ratio = _safe_div(self.cruise_distance_km, self.cruise_straight_km)
        return None if ratio is None else (ratio - 1.0) * 100.0

    @property
    def circling_distance_km(self) -> float:
        """Track flown while circling — distance spent going nowhere in particular."""
        return max(self.distance_flown_km - self.cruise_distance_km, 0.0)

    @property
    def working_band(self) -> float:
        return self.altitude_max - self.altitude_min


@dataclass(frozen=True)
class FlightMetrics:
    """Whole-flight analysis. ``legs`` carries the same four families per leg."""

    flight: Flight
    task: TaskDef
    legs: tuple[LegMetrics, ...]
    outlanded: bool
    completed: bool
    start_time: dt.datetime | None
    finish_time: dt.datetime | None
    start_altitude: float | None
    finish_altitude: float | None
    final_glide_km: float | None
    final_glide_height: float | None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    # Grouping keys are re-exposed here so a list of analyses can be pivoted by
    # task or by pilot without reaching back into the Flight.
    @property
    def task_key(self) -> str:
        return self.task.key

    @property
    def pilot_key(self) -> str:
        return self.flight.pilot_key

    @property
    def task_distance_km(self) -> float:
        return sum(leg.task_distance_km for leg in self.legs)

    @property
    def task_duration_s(self) -> float:
        return sum(leg.duration_s for leg in self.legs)

    @property
    def task_speed_kmh(self) -> float | None:
        speed = _safe_div(self.task_distance_km, self.task_duration_s)
        return None if speed is None else speed * 3600.0

    @property
    def thermals(self) -> tuple[Thermal, ...]:
        return tuple(t for leg in self.legs for t in leg.thermals)

    @property
    def thermal_count(self) -> int:
        return len(self.thermals)

    @property
    def circling_s(self) -> float:
        return sum(leg.circling_s for leg in self.legs)

    @property
    def average_climb_ms(self) -> float | None:
        gain = sum(leg.thermal_height_gain for leg in self.legs)
        return _safe_div(gain, self.circling_s)

    @property
    def percent_circling(self) -> float | None:
        ratio = _safe_div(self.circling_s, self.task_duration_s)
        return None if ratio is None else ratio * 100.0

    @property
    def distance_flown_km(self) -> float:
        return sum(leg.distance_flown_km for leg in self.legs)

    @property
    def detour_percent(self) -> float | None:
        flown = sum(leg.cruise_distance_km for leg in self.legs)
        straight = sum(leg.cruise_straight_km for leg in self.legs)
        ratio = _safe_div(flown, straight)
        return None if ratio is None else (ratio - 1.0) * 100.0

    @property
    def cruise_speed_kmh(self) -> float | None:
        distance = sum(leg.cruise_distance_km for leg in self.legs) * 1000.0
        seconds = sum(leg.cruise_s for leg in self.legs)
        speed = _safe_div(distance, seconds)
        return None if speed is None else speed * 3.6

    @property
    def glide_ratio(self) -> float | None:
        distance = sum(leg.cruise_distance_km for leg in self.legs) * 1000.0
        loss = sum(leg.cruise_height_loss for leg in self.legs)
        return _safe_div(distance, loss)


def _slice_trace(trace: list[Fix], start: dt.datetime, end: dt.datetime) -> list[Fix]:
    return [f for f in trace if start <= f["datetime"] <= end]


def _phase_bounds(phase, leg: int | None) -> Thermal | None:
    fixes = phase.fixes
    if len(fixes) < 2:
        return None
    return Thermal(
        leg=leg,
        start_time=fixes[0]["datetime"],
        end_time=fixes[-1]["datetime"],
        entry_altitude=_alt(fixes[0]),
        exit_altitude=_alt(fixes[-1]),
    )


def analyse(
    flight: Flight,
    classification_method: str = "pysoar",
    start_time_buffer: int = 0,
) -> FlightMetrics:
    """Analyse a flight against its declared task.

    :raises ValueError: if the flight carries no task, or the task is a multistart
        task, which opensoar cannot score.
    """
    if flight.task is None:
        raise ValueError(
            "flight has no declared task: the IGC file carries no LCU/LSEEYOU "
            "task lines, so there is nothing to analyse it against"
        )

    task = to_opensoar_task(flight.task, start_time_buffer=start_time_buffer)
    if getattr(task, "multistart", False):
        raise ValueError("multistart tasks are not supported by opensoar's scoring")

    trip = Trip(task, flight.trace)
    if not trip.fixes:
        raise ValueError("the trace never started the task")

    phases = FlightPhases(classification_method, flight.trace, trip)

    warnings: list[str] = []
    outlanded = trip.outlanded()
    n_legs = trip.started_legs()
    legs: list[LegMetrics] = []

    for leg in range(n_legs):
        completed = leg < trip.completed_legs()
        start_fix = trip.fixes[leg]
        end_fix = trip.fixes[leg + 1] if completed else trip.outlanding_fix
        if end_fix is None:  # pragma: no cover - defensive
            warnings.append(f"leg {leg} has no end fix and was skipped")
            continue

        leg_fixes = _slice_trace(flight.trace, start_fix["datetime"], end_fix["datetime"])
        if len(leg_fixes) < 2:
            warnings.append(f"leg {leg} has too few fixes to measure")
            continue

        thermal_phases = [t for t in (_phase_bounds(p, leg) for p in phases.thermals(leg)) if t]
        cruise_phases = phases.cruises(leg)

        circling_s = sum(t.duration_s for t in thermal_phases)
        thermal_gain = sum(max(t.height_gain, 0.0) for t in thermal_phases)

        cruise_s = 0.0
        cruise_distance = 0.0
        cruise_straight = 0.0
        cruise_loss = 0.0
        for phase in cruise_phases:
            if len(phase.fixes) < 2:
                continue
            cruise_s += _seconds(phase.fixes[0]["datetime"], phase.fixes[-1]["datetime"])
            cruise_distance += total_distance_travelled(phase.fixes)
            straight, _ = calculate_distance_bearing(phase.fixes[0], phase.fixes[-1])
            cruise_straight += straight
            _, loss = altitude_gain_and_loss(phase.fixes, DEFAULT_GPS_ALTITUDE)
            cruise_loss += loss

        altitudes = [_alt(f) for f in leg_fixes]
        gain, loss = altitude_gain_and_loss(leg_fixes, DEFAULT_GPS_ALTITUDE)
        duration = _seconds(start_fix["datetime"], end_fix["datetime"])
        distance_km = trip.distances[leg] / 1000.0 if leg < len(trip.distances) else 0.0

        legs.append(
            LegMetrics(
                index=leg,
                label=flight.task.leg_label(leg) if leg + 1 < len(flight.task.points) else f"leg {leg}",
                completed=completed,
                task_distance_km=distance_km,
                start_time=start_fix["datetime"],
                end_time=end_fix["datetime"],
                duration_s=duration,
                speed_kmh=(None if (s := _safe_div(distance_km, duration)) is None else s * 3600.0),
                thermals=tuple(thermal_phases),
                circling_s=circling_s,
                thermal_height_gain=thermal_gain,
                distance_flown_km=total_distance_travelled(leg_fixes) / 1000.0,
                cruise_s=cruise_s,
                cruise_height_loss=cruise_loss,
                cruise_distance_km=cruise_distance / 1000.0,
                cruise_straight_km=cruise_straight / 1000.0,
                altitude_start=altitudes[0],
                altitude_end=altitudes[-1],
                altitude_min=min(altitudes),
                altitude_max=max(altitudes),
                altitude_mean=sum(altitudes) / len(altitudes),
                height_gain=gain,
                height_loss=loss,
            )
        )

    start_fix = trip.fixes[0] if trip.fixes else None
    finish_fix = None if outlanded else (trip.fixes[-1] if len(trip.fixes) > 1 else None)

    final_glide_km, final_glide_height = _final_glide(phases, legs, finish_fix)

    if outlanded:
        warnings.append(f"outlanded on leg {trip.outlanding_leg()}")

    return FlightMetrics(
        flight=flight,
        task=flight.task,
        legs=tuple(legs),
        outlanded=outlanded,
        completed=not outlanded and trip.completed_legs() == flight.task.n_legs,
        start_time=start_fix["datetime"] if start_fix else None,
        finish_time=finish_fix["datetime"] if finish_fix else None,
        start_altitude=_alt(start_fix) if start_fix else None,
        finish_altitude=_alt(finish_fix) if finish_fix else None,
        final_glide_km=final_glide_km,
        final_glide_height=final_glide_height,
        warnings=tuple(warnings),
    )


def _final_glide(
    phases: FlightPhases, legs: list[LegMetrics], finish_fix: Fix | None
) -> tuple[float | None, float | None]:
    """Distance and height used from the last climb to the finish.

    Returns ``(None, None)`` for an outlanding, where there is no final glide to
    measure.
    """
    if finish_fix is None or not legs:
        return None, None

    last_thermals = legs[-1].thermals
    if not last_thermals:
        return None, None

    top_of_last_climb = last_thermals[-1]
    glide_fixes = [
        f
        for phase in phases.cruises()
        for f in phase.fixes
        if top_of_last_climb.end_time <= f["datetime"] <= finish_fix["datetime"]
    ]
    if len(glide_fixes) < 2:
        return None, None

    return (
        total_distance_travelled(glide_fixes) / 1000.0,
        _alt(glide_fixes[0]) - _alt(glide_fixes[-1]),
    )
