"""Map layer tests.

The regression these guard is subtle and silent: pydeck serialises a plain string
property into deck.gl's ``"@@=name"`` accessor-expression form. That is correct
for ``get_path``/``get_polygon`` but wrong for enum properties like the width
unit, where it leaves deck.gl with an invalid unit and renders the track as a
giant filled blob rather than a line. Nothing raises — it just draws wrong.
"""

import json

import pytest

from debrief.app.maps import _sector_ring, _view_state, flight_deck, hex_to_rgb
from debrief.app.theme import DARK, LIGHT
from debrief.core.igc import load_igc
from debrief.core.metrics import analyse


@pytest.fixture(scope="module")
def deck_spec(synthetic_igc):
    metrics = analyse(load_igc(synthetic_igc))
    return json.loads(flight_deck(metrics, LIGHT, "OpenTopoMap").to_json())


def test_only_accessor_properties_become_expressions(deck_spec):
    offenders = [
        (layer["@@type"], key, value)
        for layer in deck_spec["layers"]
        for key, value in layer.items()
        if isinstance(value, str) and value.startswith("@@=") and not key.startswith("get")
    ]
    assert offenders == []


def test_accessor_properties_are_expressions(deck_spec):
    """The flip side: getters must be expressions, or they read as constants."""
    paths = [layer for layer in deck_spec["layers"] if layer["@@type"] == "PathLayer"]
    assert paths
    assert all(layer["getPath"] == "@@=path" for layer in paths)


def test_path_widths_are_clamped_in_pixels(deck_spec):
    for layer in deck_spec["layers"]:
        if layer["@@type"] == "PathLayer":
            assert layer["widthMinPixels"] >= 1
            assert layer["widthMaxPixels"] >= layer["widthMinPixels"]


def test_expected_layers_are_present(deck_spec):
    types = [layer["@@type"] for layer in deck_spec["layers"]]
    assert types.count("TileLayer") == 1  # base map
    assert types.count("PolygonLayer") == 1  # turnpoint sectors
    assert types.count("PathLayer") == 3  # task legs, track, thermals


def test_hex_to_rgb():
    assert hex_to_rgb("#2a78d6") == [42, 120, 214, 255]
    assert hex_to_rgb("2a78d6", 128) == [42, 120, 214, 128]


def test_sector_ring_is_closed_and_circular():
    ring = _sector_ring(51.0, 14.0, 3000.0, points=36)
    assert len(ring) == 37
    assert ring[0] == pytest.approx(ring[-1])
    from pyproj import Geod

    geod = Geod(ellps="WGS84")
    for lon, lat in ring:
        assert geod.inv(14.0, 51.0, lon, lat)[2] == pytest.approx(3000.0, rel=1e-6)


def test_view_state_frames_the_whole_track(synthetic_igc):
    """The track must fit the viewport, and fill a useful part of it.

    Pinned in pixels rather than as a zoom range because the failure mode is a
    tile-size mix-up: deck.gl measures zoom against a 512 px tile, and assuming
    256 px silently doubles the scale — a plausible-looking zoom number that
    renders the track far outside the canvas.
    """
    import math

    width_px, height_px = 1040.0, 500.0
    trace = load_igc(synthetic_igc).trace
    view = _view_state(trace, width_px=width_px, height_px=height_px)

    lats = [f["lat"] for f in trace]
    lons = [f["lon"] for f in trace]
    assert min(lats) <= view.latitude <= max(lats)
    assert min(lons) <= view.longitude <= max(lons)

    px_per_degree_lon = 512.0 * 2**view.zoom / 360.0
    rendered_width = (max(lons) - min(lons)) * px_per_degree_lon
    rendered_height = (max(lats) - min(lats)) * px_per_degree_lon / math.cos(math.radians(view.latitude))

    assert rendered_width <= width_px
    assert rendered_height <= height_px
    # …and it should not be a speck in the middle: one axis fills most of the frame.
    assert max(rendered_width / width_px, rendered_height / height_px) > 0.6


def test_dark_palette_produces_different_colours(synthetic_igc):
    metrics = analyse(load_igc(synthetic_igc))
    light = json.loads(flight_deck(metrics, LIGHT, "OpenTopoMap").to_json())
    dark = json.loads(flight_deck(metrics, DARK, "OpenTopoMap").to_json())
    light_colors = [layer.get("getColor") for layer in light["layers"]]
    dark_colors = [layer.get("getColor") for layer in dark["layers"]]
    assert light_colors != dark_colors
