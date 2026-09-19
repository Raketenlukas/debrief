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
from debrief.core.metrics import FlightMetrics, fix_altitude
from debrief.core.models import Fix, TaskDef

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
