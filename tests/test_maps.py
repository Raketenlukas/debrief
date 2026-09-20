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
from debrief.app.theme import DARK, DEFAULT_BASEMAP, LIGHT
from debrief.core.igc import load_igc
from debrief.core.metrics import analyse


@pytest.fixture(scope="module")
def deck_spec(synthetic_igc):
    metrics = analyse(load_igc(synthetic_igc))
    return json.loads(flight_deck(metrics, LIGHT, DEFAULT_BASEMAP).to_json())


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
    # No TileLayer: the basemap is the map's style, not a layer.
    assert "TileLayer" not in types
    assert types.count("PolygonLayer") == 1  # turnpoint sectors
    assert types.count("PathLayer") == 2  # task legs, flown track


def test_the_basemap_is_a_style_and_the_provider_is_keyless(deck_spec):
    """map_provider=None switches the basemap off entirely, which is the bug
    that left the track drawn over a blank page."""
    assert deck_spec.get("mapProvider") == "carto"
    style = deck_spec.get("mapStyle", "")
    assert style.startswith("data:application/json,"), "expected an inline raster style"
    assert "%22raster%22" in style


def test_everything_the_user_can_point_at_is_pickable(deck_spec):
    """An unpickable layer is invisible to the cursor however good it looks."""
    for layer in deck_spec["layers"]:
        if layer["@@type"] == "TileLayer":
            continue
        assert layer.get("pickable") is True, layer["@@type"]


def test_track_is_split_into_hoverable_segments(deck_spec):
    """One path for the whole flight can only say "this is the track": deck.gl
    picks whole paths, not vertices."""
    track = [
        layer for layer in deck_spec["layers"] if layer["@@type"] == "PathLayer" and len(layer["data"]) > 20
    ]
    assert len(track) == 1
    segments = track[0]["data"]
    assert len(segments) > 50
    for segment in segments:
        assert len(segment["path"]) >= 2
        assert segment["info"]
        assert len(segment["color"]) == 4


def test_track_segments_join_without_gaps(deck_spec):
    """Adjacent segments must share their boundary point, or the drawn track
    is a dashed line with a hole at every phase change."""
    track = max(
        (layer for layer in deck_spec["layers"] if layer["@@type"] == "PathLayer"),
        key=lambda layer: len(layer["data"]),
    )
    segments = track["data"]
    # Deliberately not strict=True: the offset slice is one shorter by design.
    for first, second in zip(segments, segments[1:]):  # noqa: B905
        assert first["path"][-1] == second["path"][0]


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


def _track_colours(spec):
    track = max(
        (layer for layer in spec["layers"] if layer["@@type"] == "PathLayer"),
        key=lambda layer: len(layer["data"]),
    )
    return {tuple(segment["color"]) for segment in track["data"]}


def test_dark_palette_produces_different_colours(synthetic_igc):
    metrics = analyse(load_igc(synthetic_igc))
    light = json.loads(flight_deck(metrics, LIGHT, DEFAULT_BASEMAP).to_json())
    dark = json.loads(flight_deck(metrics, DARK, DEFAULT_BASEMAP).to_json())
    assert _track_colours(light) != _track_colours(dark)


def test_airspace_is_drawn_beneath_the_track(synthetic_igc):
    """Paint order is layer order: airspace must not cover the flight."""
    from pathlib import Path

    from debrief.core.airspace import load_openair

    airspaces = load_openair(Path(__file__).parent / "fixtures" / "sample_airspace.txt")
    metrics = analyse(load_igc(synthetic_igc))
    spec = json.loads(flight_deck(metrics, LIGHT, DEFAULT_BASEMAP, airspaces).to_json())

    types = [layer["@@type"] for layer in spec["layers"]]
    assert types.count("PolygonLayer") == 2  # airspace + turnpoint sectors

    airspace_layer = spec["layers"][0]
    assert airspace_layer["@@type"] == "PolygonLayer"
    assert len(airspace_layer["data"]) == sum(len(a.rings) for a in airspaces)
    track_index = max(i for i, layer in enumerate(spec["layers"]) if layer["@@type"] == "PathLayer")
    assert track_index > 1, "airspace must be painted before the track"

    for polygon in airspace_layer["data"]:
        assert polygon["fill"][3] < 64, "airspace fill must stay faint; they stack"
        assert polygon["line"][3] > polygon["fill"][3]
        assert polygon["info"]


def test_no_airspace_means_no_airspace_layer(synthetic_igc):
    metrics = analyse(load_igc(synthetic_igc))
    spec = json.loads(flight_deck(metrics, LIGHT, DEFAULT_BASEMAP, []).to_json())
    assert [layer["@@type"] for layer in spec["layers"]].count("PolygonLayer") == 1


def test_tooltips_are_plain_text_not_html(deck_spec):
    """Through Streamlit the deck tooltip renders as text, so markup arrives as
    visible "<b>" tags instead of bold. Caught only by looking at the running
    app, so it is pinned here."""
    for layer in deck_spec["layers"]:
        if not isinstance(layer.get("data"), list):
            continue
        for datum in layer["data"]:
            info = datum.get("info")
            if info is None:
                continue
            assert "<" not in info and ">" not in info, info


def test_every_pickable_datum_has_a_tooltip(deck_spec):
    """A pickable layer whose data lacks the shared field shows the raw
    template, because deck.gl has one tooltip for the whole deck."""
    for layer in deck_spec["layers"]:
        if not layer.get("pickable") or not isinstance(layer.get("data"), list):
            continue
        for datum in layer["data"]:
            assert datum.get("info"), f"{layer['@@type']} datum without tooltip text"
