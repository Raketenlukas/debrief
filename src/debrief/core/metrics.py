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

from debrief.core.igc import ASSUMED_SECTOR_LADDER, to_opensoar_task, with_assumed_sectors
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
class PhaseTotals:
    """Phase-derived totals over a span of flight, independent of any task.

    A free flight has no legs to sum, so the same numbers are computed once
    over the whole trace instead. Leg-based flights keep summing their legs,
    which is why this is a fallback rather than a replacement.
    """

    thermals: tuple[Thermal, ...]
    circling_s: float
    thermal_height_gain: float
    distance_flown_km: float
    cruise_s: float
    cruise_distance_km: float
    cruise_straight_km: float
    cruise_height_loss: float
    duration_s: float
    altitude_min: float
    altitude_max: float
    altitude_mean: float
    height_gain: float
    height_loss: float


def _phase_totals(fixes: list[Fix], phases: FlightPhases, leg: int | None = None) -> PhaseTotals:
    """Aggregate thermal and cruise phases over ``fixes``."""
    thermals = tuple(t for t in (_phase_bounds(p, leg) for p in phases.thermals()) if t)
    within = tuple(
        t for t in thermals if fixes[0]["datetime"] <= t.start_time and t.end_time <= fixes[-1]["datetime"]
    )

    cruise_s = cruise_distance = cruise_straight = cruise_loss = 0.0
    for phase in phases.cruises():
        span = [f for f in phase.fixes if fixes[0]["datetime"] <= f["datetime"] <= fixes[-1]["datetime"]]
        if len(span) < 2:
            continue
        cruise_s += _seconds(span[0]["datetime"], span[-1]["datetime"])
        cruise_distance += total_distance_travelled(span)
        cruise_straight += calculate_distance_bearing(span[0], span[-1])[0]
        cruise_loss += altitude_gain_and_loss(span, DEFAULT_GPS_ALTITUDE)[1]

    altitudes = [_alt(f) for f in fixes]
    gain, loss = altitude_gain_and_loss(fixes, DEFAULT_GPS_ALTITUDE)
    return PhaseTotals(
        thermals=within,
        circling_s=sum(t.duration_s for t in within),
        thermal_height_gain=sum(max(t.height_gain, 0.0) for t in within),
        distance_flown_km=total_distance_travelled(fixes) / 1000.0,
        cruise_s=cruise_s,
        cruise_distance_km=cruise_distance / 1000.0,
        cruise_straight_km=cruise_straight / 1000.0,
        cruise_height_loss=cruise_loss,
        duration_s=_seconds(fixes[0]["datetime"], fixes[-1]["datetime"]),
        altitude_min=min(altitudes),
        altitude_max=max(altitudes),
        altitude_mean=sum(altitudes) / len(altitudes),
        height_gain=gain,
        height_loss=loss,
    )


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
    # None for a free flight: no declared task, so no legs and no task metrics.
    task: TaskDef | None
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
    # Set only when there are no legs to sum, i.e. a free flight.
    overall: PhaseTotals | None = None

    # Grouping keys are re-exposed here so a list of analyses can be pivoted by
    # task or by pilot without reaching back into the Flight.
    @property
    def has_task(self) -> bool:
        return self.task is not None

    @property
    def task_key(self) -> str | None:
        return self.task.key if self.task else None

    @property
    def pilot_key(self) -> str:
        return self.flight.pilot_key

    @property
    def task_distance_km(self) -> float:
        return sum(leg.task_distance_km for leg in self.legs)

    @property
    def task_duration_s(self) -> float:
        """Time on task, or the whole flight when there is no task."""
        if self.overall is not None:
            return self.overall.duration_s
        return sum(leg.duration_s for leg in self.legs)

    @property
    def task_speed_kmh(self) -> float | None:
        speed = _safe_div(self.task_distance_km, self.task_duration_s)
        return None if speed is None else speed * 3600.0

    @property
    def thermals(self) -> tuple[Thermal, ...]:
        if self.overall is not None:
            return self.overall.thermals
        return tuple(t for leg in self.legs for t in leg.thermals)

    @property
    def thermal_count(self) -> int:
        return len(self.thermals)

    @property
    def circling_s(self) -> float:
        if self.overall is not None:
            return self.overall.circling_s
        return sum(leg.circling_s for leg in self.legs)

    @property
    def average_climb_ms(self) -> float | None:
        if self.overall is not None:
            return _safe_div(self.overall.thermal_height_gain, self.overall.circling_s)
        gain = sum(leg.thermal_height_gain for leg in self.legs)
        return _safe_div(gain, self.circling_s)

    @property
    def percent_circling(self) -> float | None:
        ratio = _safe_div(self.circling_s, self.task_duration_s)
        return None if ratio is None else ratio * 100.0

    @property
    def distance_flown_km(self) -> float:
        if self.overall is not None:
            return self.overall.distance_flown_km
        return sum(leg.distance_flown_km for leg in self.legs)

    @property
    def detour_percent(self) -> float | None:
        if self.overall is not None:
            flown, straight = self.overall.cruise_distance_km, self.overall.cruise_straight_km
        else:
            flown = sum(leg.cruise_distance_km for leg in self.legs)
            straight = sum(leg.cruise_straight_km for leg in self.legs)
        ratio = _safe_div(flown, straight)
        return None if ratio is None else (ratio - 1.0) * 100.0

    @property
    def cruise_speed_kmh(self) -> float | None:
        if self.overall is not None:
            distance, seconds = self.overall.cruise_distance_km * 1000.0, self.overall.cruise_s
        else:
            distance = sum(leg.cruise_distance_km for leg in self.legs) * 1000.0
            seconds = sum(leg.cruise_s for leg in self.legs)
        speed = _safe_div(distance, seconds)
        return None if speed is None else speed * 3.6

    @property
    def glide_ratio(self) -> float | None:
        if self.overall is not None:
            return _safe_div(self.overall.cruise_distance_km * 1000.0, self.overall.cruise_height_loss)
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

    task_def = flight.task
    task = to_opensoar_task(task_def, start_time_buffer=start_time_buffer)
    if getattr(task, "multistart", False):
        raise ValueError("multistart tasks are not supported by opensoar's scoring")

    trip = Trip(task, flight.trace)
    sector_note: str | None = None

    # An assumed sector smaller than the declared one is never entered, so a
    # completed task reads as an outlanding. Widen and retry before believing
    # it. Declared geometry is taken at its word — there is nothing to guess.
    if task_def.geometry_assumed and trip.outlanded():
        for start_radius, turnpoint_radius, finish_radius in ASSUMED_SECTOR_LADDER[1:]:
            widened = with_assumed_sectors(task_def, start_radius, turnpoint_radius, finish_radius)
            candidate_task = to_opensoar_task(widened, start_time_buffer=start_time_buffer)
            candidate_trip = Trip(candidate_task, flight.trace)
            if not candidate_trip.outlanded():
                task_def, task, trip = widened, candidate_task, candidate_trip
                sector_note = (
                    "the task completes only with wider assumed sectors "
                    f"({turnpoint_radius:.0f} m turnpoints, {finish_radius:.0f} m finish); "
                    "the declared zones were larger than the defaults"
                )
                break

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
                label=task_def.leg_label(leg) if leg + 1 < len(task_def.points) else f"leg {leg}",
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

    if sector_note:
        warnings.append(sector_note)

    if outlanded:
        warnings.append(f"outlanded on leg {trip.outlanding_leg()}")
        if task_def.geometry_assumed:
            # The task came from C records, so the sectors are defaults. A
            # default smaller than the declared one is never entered, and the
            # flight reads as an outlanding it never made.
            warnings.append(
                "this task's observation zones are assumed, not declared, so the "
                "outlanding may be an artefact of a turnpoint sector that is "
                "smaller than the one actually flown"
            )
    elif task_def.geometry_assumed:
        warnings.append(
            "observation zones are assumed, not declared: leg times and speeds "
            "depend on sector size and are approximate"
        )

    return FlightMetrics(
        flight=flight,
        task=task_def,
        legs=tuple(legs),
        outlanded=outlanded,
        completed=not outlanded and trip.completed_legs() == task_def.n_legs,
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

    # Search every leg, not just the last one. On a real task the final leg is
    # often a short run-in flown without a climb, so the last thermal frequently
    # sits on an earlier leg — looking only at legs[-1] silently reports no final
    # glide on exactly the flights where it matters most.
    all_thermals = [thermal for leg in legs for thermal in leg.thermals]
    if not all_thermals:
        return None, None

    top_of_last_climb = all_thermals[-1]
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


def summarise(flight: Flight, classification_method: str = "pysoar") -> FlightMetrics:
    """Analyse a flight with no task: a free flight, or a file with no declaration.

    Everything that does not depend on a task still holds — thermals, climb
    rates, circling share, cruise speed, achieved glide, the altitude band — so
    a flight without a declaration is still worth debriefing. The task-shaped
    fields are simply absent rather than faked.
    """
    trace = flight.trace
    if len(trace) < 2:
        raise ValueError("the trace is too short to analyse")

    phases = FlightPhases(classification_method, trace)
    totals = _phase_totals(trace, phases)

    return FlightMetrics(
        flight=flight,
        task=None,
        legs=(),
        outlanded=False,
        completed=False,
        start_time=trace[0]["datetime"],
        finish_time=trace[-1]["datetime"],
        start_altitude=_alt(trace[0]),
        finish_altitude=_alt(trace[-1]),
        final_glide_km=None,
        final_glide_height=None,
        warnings=(),
        overall=totals,
    )


def analyse_or_summarise(flight: Flight, **kwargs) -> FlightMetrics:
    """Analyse against the task if there is one, otherwise summarise the flight.

    Callers that simply want "show me this flight" should use this; it is the
    difference between a file opening and a file being rejected.
    """
    if flight.task is None:
        return summarise(flight)
    return analyse(flight, **kwargs)
