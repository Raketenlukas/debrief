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
        task=_task_to_def(task),
        source=FlightSource(
            kind="local",
            reference=str(path.resolve()),
            imported_at=dt.datetime.now(dt.UTC),
        ),
        competition=competition,
        ranking=ranking,
        headers=dict(header),
    )
