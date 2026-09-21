"""Shared map plumbing: projection, colour, framing and the tooltip.

Everything here is used by more than one map, and none of it knows what is
being drawn.
"""

from __future__ import annotations

import math

import pydeck as pdk
from pyproj import Geod

from debrief.core.models import Fix

GEOD = Geod(ellps="WGS84")

# deck.gl's zoom convention: the world is TILE_SIZE * 2**zoom pixels across.
TILE_SIZE = 512.0


def hex_to_rgb(color: str, alpha: int = 255) -> list[int]:
    color = color.lstrip("#")
    return [int(color[i : i + 2], 16) for i in (0, 2, 4)] + [alpha]


def sector_ring(lat: float, lon: float, radius: float, points: int = 72) -> list[list[float]]:
    """A geodesic circle as a deck.gl polygon ring, in [lon, lat] order."""
    ring = []
    for step in range(points + 1):
        bearing = 360.0 * step / points
        p_lon, p_lat, _ = GEOD.fwd(lon, lat, bearing, radius)
        ring.append([p_lon, p_lat])
    return ring


def view_state(
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
