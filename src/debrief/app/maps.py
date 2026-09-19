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

from debrief.app.theme import TILE_SOURCES, Palette
from debrief.core.metrics import FlightMetrics
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


def _task_layers(task: TaskDef, palette: Palette) -> list[pdk.Layer]:
    sectors = [
        {
            "name": point.name,
            "polygon": _sector_ring(point.latitude, point.longitude, point.r_max or 500.0),
        }
        for point in task.points
    ]
    legs = [
        {
            "path": [
                [task.points[i].longitude, task.points[i].latitude],
                [task.points[i + 1].longitude, task.points[i + 1].latitude],
            ]
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


def _track_layers(metrics: FlightMetrics, palette: Palette) -> list[pdk.Layer]:
    """Track split into climbs and everything else, coloured to match the barogram."""
    trace = metrics.flight.trace
    thermal_spans = [(t.start_time, t.end_time) for t in metrics.thermals]

    thermal_paths = []
    for start, end in thermal_spans:
        segment = [[f["lon"], f["lat"]] for f in trace if start <= f["datetime"] <= end]
        if len(segment) > 1:
            thermal_paths.append({"path": segment})

    full_path = [[f["lon"], f["lat"]] for f in trace]

    return [
        pdk.Layer(
            "PathLayer",
            data=[{"path": full_path}],
            get_path="path",
            get_color=hex_to_rgb(palette.cruise, 220),
            get_width=60,
            width_min_pixels=2,
            width_max_pixels=4,
        ),
        pdk.Layer(
            "PathLayer",
            data=thermal_paths,
            get_path="path",
            get_color=hex_to_rgb(palette.thermal, 240),
            get_width=90,
            width_min_pixels=3,
            width_max_pixels=6,
        ),
    ]


def flight_deck(
    metrics: FlightMetrics,
    palette: Palette,
    tile_source: str,
) -> pdk.Deck:
    """Build the map: base tiles, declared task, flown track."""
    source = TILE_SOURCES[tile_source]

    layers: list[pdk.Layer] = [
        pdk.Layer(
            "TileLayer",
            data=source["url"],
            min_zoom=0,
            max_zoom=19,
            tile_size=256,
            opacity=0.85,
        )
    ]
    if metrics.task is not None:
        layers.extend(_task_layers(metrics.task, palette))
    layers.extend(_track_layers(metrics, palette))

    return pdk.Deck(
        layers=layers,
        initial_view_state=_view_state(metrics.flight.trace),
        # The base map is the TileLayer above, so deck.gl's own basemap is off;
        # this also avoids needing a Mapbox token.
        map_provider=None,
        tooltip={"text": "{name}"},
    )
