"""Load an IGC file into the domain model.

This is the only module that knows about IGC syntax. Everything downstream works
on :class:`~debrief.core.models.Flight`.

Task reconstruction leans on ``opensoar``: SoaringSpot writes the task declaration
into ``LCU::C`` / ``LSEEYOU OZ`` comment lines, so a competition IGC is
self-describing and no separate task fetch is needed. Files without those lines
(a normal logger file from a free flight) load fine with ``task=None``.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

from aerofiles.igc import Reader
from opensoar.competition.soaringspot import get_info_from_comment_lines
from opensoar.task.aat import AAT
from opensoar.task.race_task import RaceTask
from opensoar.task.waypoint import Waypoint

from debrief.core.models import (
    CompetitionDayRef,
    Flight,
    FlightSource,
    Pilot,
    TaskDef,
    TaskPoint,
)

logger = logging.getLogger(__name__)


class IGCError(ValueError):
    """Raised when a file cannot be used for analysis."""


def _read_text(path: Path) -> str:
    # Loggers are inconsistent about encoding; opensoar uses the same fallback.
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin1")


def _task_to_def(task: RaceTask | AAT | None) -> TaskDef | None:
    if task is None:
        return None

    points = tuple(
        TaskPoint(
            name=w.name.strip(),
            latitude=w.latitude,
            longitude=w.longitude,
            r_max=w.r_max,
            angle_max=w.angle_max,
            r_min=w.r_min,
            angle_min=w.angle_min,
            is_line=w.is_line,
            sector_orientation=w.sector_orientation,
            distance_correction=w.distance_correction,
            orientation_angle=w.orientation_angle,
        )
        for w in task.waypoints
    )

    is_aat = isinstance(task, AAT)
    return TaskDef(
        points=points,
        task_type="aat" if is_aat else "race",
        t_min=getattr(task, "t_min", None) if is_aat else None,
        start_opening=getattr(task, "start_opening", None),
        timezone_hours=getattr(task, "timezone", None),
    )


def to_opensoar_task(task: TaskDef, start_time_buffer: int = 0) -> RaceTask | AAT:
    """Rebuild an opensoar task from a :class:`TaskDef`.

    ``TaskDef`` is the canonical representation; opensoar objects are constructed
    on demand for scoring. Keeping the conversion one-way-at-a-time means a task
    that later arrives from the SoaringSpot API rather than from IGC comment lines
    needs no change downstream.
    """
    waypoints = [
        Waypoint(
            name=p.name,
            latitude=p.latitude,
            longitude=p.longitude,
            r_min=p.r_min,
            angle_min=p.angle_min,
            r_max=p.r_max,
            angle_max=p.angle_max,
            is_line=p.is_line,
            sector_orientation=p.sector_orientation,
            distance_correction=p.distance_correction,
            orientation_angle=p.orientation_angle,
        )
        for p in task.points
    ]

    if task.task_type == "aat":
        if task.t_min is None:
            raise IGCError("AAT task without a minimum time cannot be scored")
        return AAT(waypoints, task.t_min, task.timezone_hours, task.start_opening, start_time_buffer)
    return RaceTask(waypoints, task.timezone_hours, task.start_opening, start_time_buffer)


# Observation zones assumed when a task comes from standard IGC C records,
# which carry no sector geometry. These are the common competition defaults
# (and match what the SoaringSpot files in this project actually declare):
# a 5 km start line, 500 m turnpoint cylinders, a 3 km finish ring.
ASSUMED_START_RADIUS = 5000.0
ASSUMED_TURNPOINT_RADIUS = 500.0
ASSUMED_FINISH_RADIUS = 3000.0


# When sectors are assumed rather than declared, one that is smaller than the
# real thing is never entered, and a completed task reads as an outlanding.
# These are tried in order, widening until the task completes. Declared
# geometry is never widened — there is nothing to guess.
ASSUMED_SECTOR_LADDER: tuple[tuple[float, float, float], ...] = (
    (ASSUMED_START_RADIUS, ASSUMED_TURNPOINT_RADIUS, ASSUMED_FINISH_RADIUS),
    (5000.0, 1000.0, 5000.0),
    (10000.0, 3000.0, 10000.0),
)


def with_assumed_sectors(
    task: TaskDef, start_radius: float, turnpoint_radius: float, finish_radius: float
) -> TaskDef:
    """A copy of ``task`` with its observation zones resized.

    Only meaningful for a task whose geometry was assumed; resizing a declared
    task would be inventing a different task.
    """
    last = len(task.points) - 1
    points = []
    for index, point in enumerate(task.points):
        if index == 0:
            radius = start_radius
        elif index == last:
            radius = finish_radius
        else:
            radius = turnpoint_radius
        points.append(replace(point, r_max=radius))
    return replace(task, points=tuple(points))


def _is_placeholder(waypoint: dict) -> bool:
    """IGC writes 0/0 for an unspecified takeoff or landing point."""
    return waypoint["latitude"] == 0.0 and waypoint["longitude"] == 0.0


def task_from_c_records(
    declaration: dict,
    start_radius: float = ASSUMED_START_RADIUS,
    turnpoint_radius: float = ASSUMED_TURNPOINT_RADIUS,
    finish_radius: float = ASSUMED_FINISH_RADIUS,
) -> TaskDef | None:
    """Rebuild a task from standard IGC ``C`` records.

    Most files declare their task this way; the ``LCU::C``/``LSEEYOU OZ``
    comment lines that :func:`load_igc` prefers are a SoaringSpot/SeeYou
    addition. Reading only those makes an ordinary competition file look like
    it has no task at all.

    The cost is that C records carry no observation zones, so the sectors are
    defaults and the task is marked ``geometry_assumed``. Sector size decides
    when a turnpoint counts as rounded, so a default that is *smaller* than the
    declared one can turn a completed task into a phantom outlanding — hence
    the radii are parameters, and :func:`debrief.core.metrics.analyse` warns
    when an assumed-geometry task comes out as an outlanding.
    """
    waypoints = list(declaration.get("waypoints") or [])
    if not waypoints:
        return None

    # A record brackets the task with takeoff and landing points, which are
    # 0/0 when unspecified.
    while waypoints and _is_placeholder(waypoints[0]):
        waypoints.pop(0)
    while waypoints and _is_placeholder(waypoints[-1]):
        waypoints.pop()

    expected = (declaration.get("num_turnpoints") or 0) + 2  # + start and finish
    if expected >= 3 and len(waypoints) == expected + 2:
        # Takeoff and landing were given explicitly rather than as 0/0.
        waypoints = waypoints[1:-1]
    if len(waypoints) < 3:
        return None
    if expected >= 3 and len(waypoints) != expected:
        # The declaration does not line up with its own turnpoint count. Better
        # to report no task than to silently score the wrong one.
        logger.warning(
            "C record declares %d turnpoints but yields %d task points; ignoring",
            declaration.get("num_turnpoints"),
            len(waypoints),
        )
        return None

    last = len(waypoints) - 1
    points = []
    for index, waypoint in enumerate(waypoints):
        if index == 0:
            radius, is_line, orientation = start_radius, True, "next"
        elif index == last:
            radius, is_line, orientation = finish_radius, False, "previous"
        else:
            radius, is_line, orientation = turnpoint_radius, False, "symmetrical"
        points.append(
            TaskPoint(
                name=(waypoint.get("description") or f"TP{index}").strip() or f"TP{index}",
                latitude=waypoint["latitude"],
                longitude=waypoint["longitude"],
                r_max=radius,
                angle_max=180,
                is_line=is_line,
                sector_orientation=orientation,
            )
        )

    return TaskDef(points=tuple(points), task_type="race", geometry_assumed=True)


def _flight_date(parsed: dict[str, Any], trace: list[dict[str, Any]]) -> dt.date:
    header = parsed.get("header", [[], {}])[1]
    date = header.get("utc_date")
    if date is not None:
        return date
    if trace:
        return trace[0]["datetime"].date()
    raise IGCError("file has neither an HFDTE header nor any fixes")


def load_igc(
    path: str | Path,
    competition: CompetitionDayRef | None = None,
    ranking: int | str | None = None,
    start_time_buffer: int = 0,
) -> Flight:
    """Parse one IGC file into a :class:`Flight`.

    :param start_time_buffer: seconds of tolerance on the start gate, matching the
        scorer's own leniency. Passed through to task reconstruction.
    """
    path = Path(path)
    if not path.is_file():
        raise IGCError(f"no such file: {path}")

    try:
        parsed = Reader(skip_duplicates=True).read(_read_text(path).splitlines(keepends=True))
    except Exception as exc:  # aerofiles raises a variety of types
        raise IGCError(f"{path.name}: could not parse as IGC ({exc})") from exc

    errors, trace = parsed["fix_records"]
    if not trace:
        raise IGCError(f"{path.name}: contains no usable B records ({errors})")

    date = _flight_date(parsed, trace)
    task, contest_info, competitor_info = get_info_from_comment_lines(parsed, date, start_time_buffer)
    task_def = _task_to_def(task)

    if task_def is None:
        # No SoaringSpot comment lines. Fall back to the standard C record
        # declaration, which is how most loggers write a task.
        declaration_errors, declaration = parsed.get("task", ([], {}))
        if not declaration_errors:
            task_def = task_from_c_records(declaration)

    header = parsed.get("header", [[], {}])[1]
    pilot = Pilot(
        # LCU comment lines are SoaringSpot's restatement of the header and are
        # usually the cleaner of the two; fall back to the H records.
        name=competitor_info.get("pilot_name") or header.get("pilot"),
        competition_id=competitor_info.get("competition_id") or header.get("competition_id"),
        glider_model=competitor_info.get("plane_model") or header.get("glider_model"),
        glider_registration=header.get("glider_registration"),
    )

    if competition is None and contest_info.get("competition_class"):
        competition = CompetitionDayRef(
            competition=path.parent.name or "unknown",
            plane_class=contest_info["competition_class"],
            date=date,
        )

    return Flight(
        trace=trace,
        pilot=pilot,
        date=date,
        task=task_def,
        source=FlightSource(
            kind="local",
            reference=str(path.resolve()),
            imported_at=dt.datetime.now(dt.UTC),
        ),
        competition=competition,
        ranking=ranking,
        headers=dict(header),
    )
