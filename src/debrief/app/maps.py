"""pydeck map of the flight and its task.

deck.gl is used here rather than Leaflet because an IGC file is tens of thousands
of points and because the same layer model carries over to a React front end
later — pydeck now, deck.gl in the browser then, with the layer definitions
essentially unchanged.
"""

from __future__ import annotations

import math

import pydeck as pdk
from pyproj import Geod

from debrief.app.theme import Palette, airspace_family, basemap_style
from debrief.core.airspace import Airspace
from debrief.core.compare import DayComparison
from debrief.core.field import BestStretch, Cell, CompositeBest
from debrief.core.metrics import FlightMetrics, fix_altitude
from debrief.core.models import Fix, TaskDef
from debrief.core.progress import SEGMENT_M, DeltaSegment, PilotProgress, ProgressComparison

GEOD = Geod(ellps="WGS84")

# deck.gl's zoom convention: the world is TILE_SIZE * 2**zoom pixels across.
TILE_SIZE = 512.0


def hex_to_rgb(color: str, alpha: int = 255) -> list[int]:
    color = color.lstrip("#")
    return [int(color[i : i + 2], 16) for i in (0, 2, 4)] + [alpha]


def _sector_ring(lat: float, lon: float, radius: float, points: int = 72) -> list[list[float]]:
    """A geodesic circle as a deck.gl polygon ring, in [lon, lat] order."""
    ring = []
    for step in range(points + 1):
        bearing = 360.0 * step / points
        p_lon, p_lat, _ = GEOD.fwd(lon, lat, bearing, radius)
        ring.append([p_lon, p_lat])
    return ring


def _view_state(
    trace: list[Fix],
    width_px: float = 1040.0,
    height_px: float = 500.0,
    fill: float = 0.82,
) -> pdk.ViewState:
    """Frame the whole track with a margin.

    deck.gl defines zoom against a 512-pixel tile (the Mapbox/MapLibre
    convention), NOT the 256-pixel tile of classic XYZ raster tiles — so at zoom
    z the world is 512 * 2**z pixels wide. Using 256 here computes a zoom exactly
    one level too high and the track overflows the viewport.

    A degree of latitude covers 1/cos(lat) as many pixels as a degree of
    longitude, so both axes are fitted and the looser zoom wins.

    ``pydeck.data_utils.compute_view`` is deliberately not used: it snaps to
    integer zoom levels and centres on the geometric mean of the points, which
    for a flight track pulls the view toward wherever the glider circled most.
    """
    lats = [f["lat"] for f in trace]
    lons = [f["lon"] for f in trace]
    center_lat = (max(lats) + min(lats)) / 2.0
    lat_span = max(max(lats) - min(lats), 1e-4)
    lon_span = max(max(lons) - min(lons), 1e-4)

    scale_lon = fill * width_px * 360.0 / (TILE_SIZE * lon_span)
    scale_lat = fill * height_px * 360.0 * math.cos(math.radians(center_lat)) / (TILE_SIZE * lat_span)
    zoom = math.log2(max(min(scale_lon, scale_lat), 1.0))

    return pdk.ViewState(
        latitude=center_lat,
        longitude=(max(lons) + min(lons)) / 2.0,
        zoom=max(min(zoom, 14.0), 3.0),
        pitch=0,
    )


# Every pickable datum carries its tooltip under the same key. deck.gl has one
# tooltip template for the whole deck, so a layer using a different field name
# would render the template literally instead of its text.
#
# Plain text, not HTML: through Streamlit the tooltip is rendered as text, so
# markup arrives as visible "<b>" tags rather than bold. Line breaks are real
# newlines and the style makes them render.
TOOLTIP = {
    "text": "{info}",
    "style": {
        "fontSize": "12px",
        "maxWidth": "320px",
        "whiteSpace": "pre-line",
        "lineHeight": "1.4",
    },
}


def _task_layers(task: TaskDef, palette: Palette) -> list[pdk.Layer]:
    sectors = [
        {
            "name": point.name,
            "info": f"{point.name}\nturnpoint {index} · radius {point.r_max or 0:.0f} m",
            "polygon": _sector_ring(point.latitude, point.longitude, point.r_max or 500.0),
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


def _airspace_layers(airspaces: list[Airspace], palette: Palette) -> list[pdk.Layer]:
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


def _track_layers(
    metrics: FlightMetrics, palette: Palette, max_fixes_per_segment: int = 40
) -> list[pdk.Layer]:
    """The flown track, split into hoverable segments coloured by phase.

    A deck.gl PathLayer picks whole paths, not vertices, so one path for the
    whole flight can only ever say "this is the track". Chopping it into short
    segments makes each one carry its own time, altitude and climb rate, which
    is what makes the map worth hovering over.
    """
    trace = metrics.flight.trace
    thermal_spans = [(t.start_time, t.end_time) for t in metrics.thermals]

    def is_thermal(fix: Fix) -> bool:
        return any(start <= fix["datetime"] <= end for start, end in thermal_spans)

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

    for fix in trace:
        phase = is_thermal(fix)
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


def flight_deck(
    metrics: FlightMetrics,
    palette: Palette,
    basemap: str,
    airspaces: list[Airspace] | None = None,
) -> pdk.Deck:
    """Build the map: basemap, airspace, declared task, flown track."""
    layers: list[pdk.Layer] = []
    # Order is paint order: airspace under the task, task under the track, so
    # the flight is never hidden by what it was flying through.
    if airspaces:
        layers.extend(_airspace_layers(airspaces, palette))
    if metrics.task is not None:
        layers.extend(_task_layers(metrics.task, palette))
    layers.extend(_track_layers(metrics, palette))

    return pdk.Deck(
        layers=layers,
        initial_view_state=_view_state(metrics.flight.trace),
        # The basemap is the map's own style, not a layer. map_provider="carto"
        # selects a keyless provider; map_provider=None switches the basemap off
        # entirely, which is what left the track floating on a blank page.
        map_provider="carto",
        map_style=basemap_style(basemap),
        tooltip=TOOLTIP,
    )


def comparison_deck(
    day: DayComparison,
    palette: Palette,
    basemap: str,
    airspaces: list[Airspace] | None = None,
) -> pdk.Deck:
    """Several pilots' tracks on one map, one colour each.

    Each track is a single path rather than the hoverable segments of the
    single-flight map: with a field on screen the question is which line is
    whose and where they diverged, not what the vario read at 14:32. The
    single-flight view keeps the detail.
    """
    layers: list[pdk.Layer] = []
    if airspaces:
        layers.extend(_airspace_layers(airspaces, palette))
    if day.flights and day.flights[0].task is not None:
        layers.extend(_task_layers(day.flights[0].task, palette))

    tracks = []
    for index, metrics in enumerate(day.flights):
        speed = metrics.task_speed_kmh
        tracks.append(
            {
                "path": [[f["lon"], f["lat"]] for f in metrics.flight.trace],
                "color": hex_to_rgb(palette.leg_color(index), 225),
                "info": (
                    f"{metrics.flight.pilot.label}\n"
                    + (f"{speed:.1f} km/h on task" if speed else "no task speed")
                ),
            }
        )

    if tracks:
        layers.append(
            pdk.Layer(
                "PathLayer",
                data=tracks,
                get_path="path",
                get_color="color",
                get_width=60,
                width_min_pixels=2,
                width_max_pixels=4,
                pickable=True,
                auto_highlight=True,
            )
        )

    # Frame every track, not just the first: pilots diverge, and a view fitted
    # to one of them cuts the others off exactly where it got interesting.
    all_fixes = [fix for metrics in day.flights for fix in metrics.flight.trace]
    view = _view_state(all_fixes) if all_fixes else pdk.ViewState(latitude=0, longitude=0, zoom=1)

    return pdk.Deck(
        layers=layers,
        initial_view_state=view,
        map_provider="carto",
        map_style=basemap_style(basemap),
        tooltip=TOOLTIP,
    )


# Where a time difference starts being worth seeing, and where it is serious —
# per kilometre of course, so the thresholds follow the stretch length the
# reader chose. A racing glider covers a kilometre in roughly 35 s, so 1 s/km
# is about 3% off the pace and 4 s/km about 11%: the same judgement whether a
# stretch is two kilometres or twenty-five, which a fixed threshold would not
# be. (Fix them instead, and coarse stretches come out red from end to end
# while fine ones never leave the neutral band.)
#
# Proportional to the stretch, but not to the day: a scale that restretched
# itself to each day's spread would make every day look equally dramatic, and
# the point of a debrief is to tell a day you flew well from one you did not.
TIME_SOFT_S_PER_KM = 1.0
TIME_HARD_S_PER_KM = 4.0

# Height against the reference at the same point on task. Fifty metres is
# noise between two gliders; two hundred is a different decision about where to
# be, and usually the cause of which a lost minute is the symptom. Fixed, not
# per kilometre: height is a state you are in, not a rate you accumulate.
HEIGHT_SOFT_M = 50.0
HEIGHT_HARD_M = 200.0

PROGRESS_TIME = "time"
PROGRESS_HEIGHT = "height"


def progress_thresholds(mode: str, segment_m: float = SEGMENT_M) -> tuple[float, float, str]:
    """``(soft, hard, unit)`` for a progress mode at this stretch length."""
    if mode == PROGRESS_HEIGHT:
        return HEIGHT_SOFT_M, HEIGHT_HARD_M, " m"
    kilometres = segment_m / 1000.0
    return TIME_SOFT_S_PER_KM * kilometres, TIME_HARD_S_PER_KM * kilometres, " s"


def _segment_loss(segment: DeltaSegment, mode: str) -> float | None:
    """The signed quantity this mode colours, with **positive meaning worse**.

    For height that means flipping the sign: being *below* the reference is the
    bad one, and a scale whose red arm meant "higher" would read backwards.
    """
    if mode == PROGRESS_HEIGHT:
        height = segment.height_delta_m
        return None if height is None else -height
    return segment.lost_s


def _progress_info(segment: DeltaSegment, reference_label: str, ruler) -> str:
    lost = segment.lost_s
    height = segment.height_delta_m
    lines = [
        f"{segment.start.time:%H:%M:%S} → {segment.end.time:%H:%M:%S}",
        ruler.describe(segment.end.distance_m),
    ]
    if lost is None:
        lines.append(f"no comparison: {reference_label} never got this far")
    else:
        verb = "lost" if lost >= 0 else "gained"
        lines.append(
            f"{verb} {abs(lost):.0f} s over {segment.distance_km:.1f} km of task "
            f"({segment.seconds / 60.0:.0f} min of flying)"
        )
    if height is not None:
        side = "above" if height >= 0 else "below"
        lines.append(f"{abs(height):.0f} m {side} {reference_label} here")
    return "\n".join(lines)


def progress_deck(
    progress: ProgressComparison,
    subject: PilotProgress,
    palette: Palette,
    basemap: str,
    mode: str = PROGRESS_TIME,
    airspaces: list[Airspace] | None = None,
    highlights: int = 5,
    segment_m: float = SEGMENT_M,
) -> pdk.Deck:
    """One pilot's track, coloured by what each stretch of course cost.

    Deliberately one pilot rather than the whole selection. The diverging scale
    spends the colour channel on *polarity*, so it has none left for identity:
    two tracks both coloured by their gap would be indistinguishable from each
    other. The gap chart carries the whole field; this carries the geography.

    Width doubles as a magnitude channel. Under colour-blind simulation the red
    arm collapses toward the grey midpoint sooner than the blue one, so a badly
    lost stretch has to be visible as more than a hue.
    """
    soft, hard, _unit = progress_thresholds(mode, segment_m)
    reference_label = progress.reference_label

    layers: list[pdk.Layer] = []
    if airspaces:
        layers.extend(_airspace_layers(airspaces, palette))
    if subject.metrics.task is not None:
        layers.extend(_task_layers(subject.metrics.task, palette))

    # The reference's own track, so "where you were when you lost it" has
    # something to be relative to. Thin and muted: it is context, not a subject.
    # A field reference is a composite of many pilots and has no track to draw.
    if not progress.reference.synthetic and progress.reference.longitude:
        layers.append(
            pdk.Layer(
                "PathLayer",
                data=[
                    {
                        "path": [
                            [lon, lat]
                            for lon, lat in zip(
                                progress.reference.longitude, progress.reference.latitude, strict=True
                            )
                        ],
                        "info": f"{reference_label} (the reference)",
                    }
                ],
                get_path="path",
                get_color=hex_to_rgb(palette.ink_muted, 130),
                get_width=30,
                width_min_pixels=1,
                width_max_pixels=2,
                pickable=True,
            )
        )

    pieces = []
    for segment in subject.segments:
        loss = _segment_loss(segment, mode)
        colour = palette.diverging.color(loss, soft, hard)
        magnitude = 0.0 if loss is None else min(abs(loss) / hard, 1.0)
        pieces.append(
            {
                "path": segment.path,
                "color": hex_to_rgb(colour, 235),
                "width": 45 + 110 * magnitude,
                "info": _progress_info(segment, reference_label, progress.ruler),
            }
        )

    if pieces:
        layers.append(
            pdk.Layer(
                "PathLayer",
                data=pieces,
                get_path="path",
                get_color="color",
                get_width="width",
                # Always the thickest line on the map. Where a stretch cost
                # nothing it is drawn in the same grey as the reference and the
                # task legs, and without a width floor the eye has three
                # identical hairlines and no way to tell which flight is which.
                width_min_pixels=3,
                width_max_pixels=10,
                pickable=True,
                auto_highlight=True,
            )
        )

    # The worst stretches, marked so they are found rather than hunted for. Ringed
    # in the chart surface colour: these sit on top of the track they belong to,
    # and without the ring the mark and the line merge into one blob.
    worst = [s for s in subject.ranked_segments(worst_first=True) if (_segment_loss(s, mode) or 0) > soft]
    marks = []
    for rank, segment in enumerate(worst[:highlights], start=1):
        loss = _segment_loss(segment, mode)
        marks.append(
            {
                "position": [segment.end.longitude, segment.end.latitude],
                "color": hex_to_rgb(palette.diverging.color(loss, soft, hard), 255),
                "radius": 260 - 22 * rank,
                "info": f"#{rank} worst stretch\n" + _progress_info(segment, reference_label, progress.ruler),
            }
        )
    if marks:
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                data=marks,
                get_position="position",
                get_fill_color="color",
                get_line_color=hex_to_rgb(palette.surface, 255),
                get_radius="radius",
                radius_min_pixels=5,
                radius_max_pixels=11,
                stroked=True,
                line_width_min_pixels=2,
                pickable=True,
            )
        )

    fixes = list(subject.metrics.flight.trace)
    view = _view_state(fixes) if fixes else pdk.ViewState(latitude=0, longitude=0, zoom=1)
    return pdk.Deck(
        layers=layers,
        initial_view_state=view,
        map_provider="carto",
        map_style=basemap_style(basemap),
        tooltip=TOOLTIP,
    )


# --- the field's day ---------------------------------------------------------

FIELD_CLIMBS = "climbs"
FIELD_LINES = "lines"

# Climb rates are bounded by physics and by what a competition day looks like:
# under 0.5 m/s nobody stays, over 4 m/s is a very good day anywhere in Europe.
# A fixed range rather than the day's own min and max, so a weak day looks weak
# instead of being restretched into the same picture as a strong one.
CLIMB_LOW_MS = 0.5
CLIMB_HIGH_MS = 4.0


def _cell_layers(
    cells: list[Cell],
    palette: Palette,
    low: float,
    high: float,
    value_of,
    info_of,
) -> list[pdk.Layer]:
    """A grid of squares, filled on the sequential ramp.

    Filled polygons rather than a smooth heatmap. A heatmap blurs *how many*
    into *how strong* — two mediocre climbs glow like one good one — and the
    question here is which of the two you are looking at. Squares keep the
    aggregate honest and let the tooltip carry the count.
    """
    if not cells:
        return []

    data = []
    for cell in cells:
        value = value_of(cell)
        colour = hex_to_rgb(palette.sequential.color(value, low, high), 190)
        data.append(
            {
                "polygon": [list(point) for point in cell.polygon],
                "fill": colour,
                "info": info_of(cell),
            }
        )

    return [
        pdk.Layer(
            "PolygonLayer",
            data=data,
            get_polygon="polygon",
            filled=True,
            stroked=True,
            get_fill_color="fill",
            # The pale end of the ramp sits at 2:1 on the page by design; over
            # a satellite tile it needs an edge to be a shape at all.
            get_line_color=hex_to_rgb(palette.ink_muted, 90),
            line_width_min_pixels=1,
            pickable=True,
            auto_highlight=True,
        )
    ]


def _climb_info(cell: Cell) -> str:
    return (
        f"{cell.mean:.1f} m/s average over {cell.count} climb"
        f"{'' if cell.count == 1 else 's'}\n"
        f"best {cell.best:.1f} m/s · {cell.pilot_count} pilot"
        f"{'' if cell.pilot_count == 1 else 's'} stopped here"
    )


def _lines_info(cell: Cell) -> str:
    return (
        f"{cell.pilot_count} pilot{'' if cell.pilot_count == 1 else 's'} crossed here\n"
        f"{cell.count} samples · typically {cell.median:.0f} m"
    )


def field_deck(
    cells: list[Cell],
    task: TaskDef | None,
    palette: Palette,
    basemap: str,
    mode: str = FIELD_CLIMBS,
    airspaces: list[Airspace] | None = None,
) -> pdk.Deck:
    """The whole field's day as a grid: where the lift was, or where they flew."""
    layers: list[pdk.Layer] = []
    if airspaces:
        layers.extend(_airspace_layers(airspaces, palette))

    if mode == FIELD_CLIMBS:
        layers.extend(
            _cell_layers(cells, palette, CLIMB_LOW_MS, CLIMB_HIGH_MS, lambda c: c.mean, _climb_info)
        )
    else:
        # Density has no natural ceiling, so the range comes from the day: the
        # busiest cell is the top of the scale and everything reads against it.
        busiest = max((c.pilot_count for c in cells), default=1)
        layers.extend(
            _cell_layers(cells, palette, 1.0, max(float(busiest), 2.0), lambda c: c.pilot_count, _lines_info)
        )

    # The task on top of the grid, so "where the lift was" can be read against
    # "where you had to go" — which is the whole tactical question.
    if task is not None:
        layers.extend(_task_layers(task, palette))

    corners = [{"lat": lat, "lon": lon} for cell in cells for lon, lat in (cell.polygon[0], cell.polygon[2])]
    view = _view_state(corners) if corners else pdk.ViewState(latitude=0, longitude=0, zoom=1)
    return pdk.Deck(
        layers=layers,
        initial_view_state=view,
        map_provider="carto",
        map_style=basemap_style(basemap),
        tooltip=TOOLTIP,
    )


def _stretch_info(stretch: BestStretch) -> str:
    speed = stretch.speed_kmh
    climb = stretch.height_change
    lines = [
        f"km {stretch.start_m / 1000:.0f}–{stretch.end_m / 1000:.0f} of the course",
        f"{stretch.pilot}" + (f" · {stretch.glider}" if stretch.glider else ""),
        f"{speed:.0f} km/h" if speed else "no speed",
    ]
    if climb is not None:
        lines.append(f"{climb:+.0f} m over the stretch")
    return "\n".join(lines)


def composite_deck(
    composite: CompositeBest,
    palette: Palette,
    basemap: str,
    airspaces: list[Airspace] | None = None,
    task: TaskDef | None = None,
) -> pdk.Deck:
    """The day's ceiling: the fastest crossing of every stretch, stitched up.

    Coloured by speed, not by pilot. Colour spent on identity would cap the
    useful field at three — any two stretches can end up side by side on a map,
    which is the all-pairs case — and would answer "who" at the cost of "where
    was the day quick", which is the question a route is asked. The contributor
    is in the tooltip and in the table below it.

    The joins between stretches are left visible. Two consecutive stretches
    flown by different pilots do not meet, and drawing a line between them
    would manufacture a flight that nobody made.
    """
    speeds = [s.speed_kmh for s in composite.stretches if s.speed_kmh]
    low = min(speeds) if speeds else 0.0
    high = max(speeds) if speeds else 1.0

    layers: list[pdk.Layer] = []
    if airspaces:
        layers.extend(_airspace_layers(airspaces, palette))
    if task is not None:
        layers.extend(_task_layers(task, palette))

    paths = [
        {
            "path": [list(point) for point in stretch.path],
            "color": hex_to_rgb(palette.sequential.color(stretch.speed_kmh, low, high), 235),
            "info": _stretch_info(stretch),
        }
        for stretch in composite.stretches
        if len(stretch.path) >= 2
    ]
    if paths:
        layers.append(
            pdk.Layer(
                "PathLayer",
                data=paths,
                get_path="path",
                get_color="color",
                get_width=110,
                width_min_pixels=3,
                width_max_pixels=8,
                pickable=True,
                auto_highlight=True,
            )
        )

    # Where the baton changes hands. These are the joins that make the
    # composite a composite rather than a flight.
    handovers = []
    for before, after in zip(composite.stretches, composite.stretches[1:], strict=False):
        if before.pilot == after.pilot or not after.path:
            continue
        handovers.append(
            {
                "position": list(after.path[0]),
                "info": f"km {after.start_m / 1000:.0f}: {before.pilot} → {after.pilot}",
            }
        )
    if handovers:
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                data=handovers,
                get_position="position",
                get_fill_color=hex_to_rgb(palette.surface, 255),
                get_line_color=hex_to_rgb(palette.ink_secondary, 255),
                get_radius=200,
                radius_min_pixels=4,
                radius_max_pixels=8,
                stroked=True,
                filled=True,
                line_width_min_pixels=2,
                pickable=True,
            )
        )

    corners = [{"lat": lat, "lon": lon} for stretch in composite.stretches for lon, lat in stretch.path]
    view = _view_state(corners) if corners else pdk.ViewState(latitude=0, longitude=0, zoom=1)
    return pdk.Deck(
        layers=layers,
        initial_view_state=view,
        map_provider="carto",
        map_style=basemap_style(basemap),
        tooltip=TOOLTIP,
    )
