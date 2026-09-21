"""Layers that appear on more than one map: the task, airspace, the track.

A layer here answers "what is on the ground and what did the glider do"; the
modules that build whole decks decide which of them a particular question
needs.
"""

from __future__ import annotations

import pydeck as pdk

from debrief.app.maps.base import GEOD, hex_to_rgb, sector_ring
from debrief.app.theme import Palette, airspace_family
from debrief.core.airspace import Airspace
from debrief.core.metrics import FlightMetrics, fix_altitude
from debrief.core.models import Fix, TaskDef
from debrief.core.trace import inside_mask


def task_layers(task: TaskDef, palette: Palette) -> list[pdk.Layer]:
    sectors = [
        {
            "name": point.name,
            "info": f"{point.name}\nturnpoint {index} · radius {point.r_max or 0:.0f} m",
            "polygon": sector_ring(point.latitude, point.longitude, point.r_max or 500.0),
        }
        for index, point in enumerate(task.points)
    ]
    legs = [
        {
            "info": f"{task.leg_label(i)}\ntask leg {i + 1}",
            "path": [
                [task.points[i].longitude, task.points[i].latitude],
                [task.points[i + 1].longitude, task.points[i + 1].latitude],
            ],
        }
        for i in range(task.n_legs)
    ]
    ink = hex_to_rgb(palette.ink_muted, 200)

    return [
        pdk.Layer(
            "PathLayer",
            data=legs,
            get_path="path",
            get_color=ink,
            # Widths are in metres (deck.gl's default) and clamped in pixels.
            # Passing the unit as a plain string does NOT work: pydeck
            # serialises it as "@@=pixels", a deck.gl accessor expression,
            # leaving the unit invalid and the rendered width meaningless.
            get_width=40,
            width_min_pixels=1,
            width_max_pixels=2,
            pickable=True,
        ),
        pdk.Layer(
            "PolygonLayer",
            data=sectors,
            get_polygon="polygon",
            filled=True,
            stroked=True,
            get_fill_color=[*ink[:3], 26],
            get_line_color=ink,
            line_width_min_pixels=1,
            pickable=True,
        ),
    ]


def airspace_layers(airspaces: list[Airspace], palette: Palette) -> list[pdk.Layer]:
    """One polygon per airspace ring, coloured by how restrictive it is.

    Fills are deliberately faint: airspace stacks vertically, so a busy area is
    many overlapping polygons and an opaque fill would bury the flight track.
    The outline carries the shape; the fill only hints at density.
    """
    polygons = []
    for airspace in airspaces:
        family = airspace_family(airspace.airspace_class, airspace.airspace_type)
        colour = hex_to_rgb(palette.airspace[family])
        limits = f"{airspace.floor.text} \u2192 {airspace.ceiling.text}"
        klass = airspace.airspace_class or airspace.airspace_type or "?"
        for ring in airspace.rings:
            polygons.append(
                {
                    "polygon": ring,
                    "fill": [*colour[:3], 20],
                    "line": [*colour[:3], 190],
                    "info": f"{airspace.name}\nclass {klass} · {family}\n{limits}",
                }
            )

    if not polygons:
        return []

    return [
        pdk.Layer(
            "PolygonLayer",
            data=polygons,
            get_polygon="polygon",
            filled=True,
            stroked=True,
            get_fill_color="fill",
            get_line_color="line",
            line_width_min_pixels=1,
            get_line_width=30,
            pickable=True,
            auto_highlight=True,
        )
    ]


def _segment_info(fixes: list[Fix], phase: str) -> str:
    start, end = fixes[0], fixes[-1]
    seconds = (end["datetime"] - start["datetime"]).total_seconds()
    climb = (fix_altitude(end) - fix_altitude(start)) / seconds if seconds else 0.0
    distance = GEOD.inv(start["lon"], start["lat"], end["lon"], end["lat"])[2]
    speed = (distance / seconds * 3.6) if seconds else 0.0
    return (
        f"{phase} {start['datetime']:%H:%M:%S}\n"
        f"{fix_altitude(start):.0f} m \u2192 {fix_altitude(end):.0f} m"
        f" ({climb:+.1f} m/s)\n{speed:.0f} km/h ground"
    )


def track_layers(
    metrics: FlightMetrics, palette: Palette, max_fixes_per_segment: int = 40
) -> list[pdk.Layer]:
    """The flown track, split into hoverable segments coloured by phase.

    A deck.gl PathLayer picks whole paths, not vertices, so one path for the
    whole flight can only ever say "this is the track". Chopping it into short
    segments makes each one carry its own time, altitude and climb rate, which
    is what makes the map worth hovering over.
    """
    trace = metrics.flight.trace
    circling = inside_mask(trace, [(t.start_time, t.end_time) for t in metrics.thermals])

    segments: list[dict] = []
    current: list[Fix] = []
    current_phase: bool | None = None

    def flush() -> None:
        if len(current) < 2:
            return
        phase = "Climb" if current_phase else "Cruise"
        colour = palette.thermal if current_phase else palette.cruise
        segments.append(
            {
                "path": [[f["lon"], f["lat"]] for f in current],
                "color": hex_to_rgb(colour, 230),
                "width": 90 if current_phase else 60,
                "info": _segment_info(current, phase),
            }
        )

    for index, fix in enumerate(trace):
        phase = bool(circling[index])
        if current_phase is None:
            current_phase = phase
        # Break on a phase change, or when a segment gets long enough that its
        # summary would stop describing any particular part of it.
        if phase != current_phase or len(current) >= max_fixes_per_segment:
            current.append(fix)  # share the boundary fix so the line has no gap
            flush()
            current = [fix]
            current_phase = phase
        else:
            current.append(fix)
    flush()

    return [
        pdk.Layer(
            "PathLayer",
            data=segments,
            get_path="path",
            get_color="color",
            get_width="width",
            width_min_pixels=2,
            width_max_pixels=6,
            pickable=True,
            auto_highlight=True,
        )
    ]
