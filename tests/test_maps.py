"""Map layer tests.

The regression these guard is subtle and silent: pydeck serialises a plain string
property into deck.gl's ``"@@=name"`` accessor-expression form. That is correct
for ``get_path``/``get_polygon`` but wrong for enum properties like the width
unit, where it leaves deck.gl with an invalid unit and renders the track as a
giant filled blob rather than a line. Nothing raises — it just draws wrong.
"""

import itertools
import json

import pytest

from debrief.app.maps import flight_deck, hex_to_rgb, sector_ring, view_state
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
    for first, second in itertools.pairwise(segments):
        assert first["path"][-1] == second["path"][0]


def test_hex_to_rgb():
    assert hex_to_rgb("#2a78d6") == [42, 120, 214, 255]
    assert hex_to_rgb("2a78d6", 128) == [42, 120, 214, 128]


def test_sector_ring_is_closed_and_circular():
    ring = sector_ring(51.0, 14.0, 3000.0, points=36)
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
    view = view_state(trace, width_px=width_px, height_px=height_px)

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


# --- the "where the time went" map -------------------------------------------


@pytest.fixture(scope="module")
def progress(synthetic_igc, second_pilot_igc):
    from debrief.core.metrics import analyse_or_summarise
    from debrief.core.progress import ProgressComparison

    fast = analyse_or_summarise(load_igc(synthetic_igc))
    slow = analyse_or_summarise(load_igc(second_pilot_igc))
    return ProgressComparison.build([fast, slow], reference=fast)


@pytest.fixture(scope="module")
def progress_spec(progress):
    from debrief.app.maps import progress_deck

    subject = next(p for p in progress.pilots if not p.is_reference)
    return json.loads(progress_deck(progress, subject, LIGHT, DEFAULT_BASEMAP).to_json())


def test_the_progress_map_keeps_its_accessors_straight(progress_spec):
    offenders = [
        (layer["@@type"], key, value)
        for layer in progress_spec["layers"]
        for key, value in layer.items()
        if isinstance(value, str) and value.startswith("@@=") and not key.startswith("get")
    ]
    assert offenders == []


def test_the_progress_map_draws_the_reference_and_the_marks(progress_spec):
    kinds = [layer["@@type"] for layer in progress_spec["layers"]]
    assert kinds.count("PathLayer") >= 3  # task legs, the reference, the coloured track
    assert "ScatterplotLayer" in kinds  # the worst stretches


def test_a_field_reference_draws_no_reference_track(synthetic_igc, second_pilot_igc):
    """A virtual best is stitched from several pilots and was never flown, so
    there is no line to draw — and drawing one would be a lie about a flight."""
    from debrief.app.maps import progress_deck
    from debrief.core.metrics import analyse_or_summarise
    from debrief.core.progress import FIELD_BEST, ProgressComparison

    flights = [analyse_or_summarise(load_igc(p)) for p in (synthetic_igc, second_pilot_igc)]
    progress = ProgressComparison.build(flights, reference=FIELD_BEST)
    spec = json.loads(progress_deck(progress, progress.pilots[0], LIGHT, DEFAULT_BASEMAP).to_json())

    labels = [
        datum.get("info", "")
        for layer in spec["layers"]
        for datum in (layer["data"] if isinstance(layer.get("data"), list) else [])
    ]
    assert not any("the reference" in text for text in labels)


def test_losing_time_is_red_and_gaining_it_is_blue(progress):
    """The one thing a diverging scale must never get backwards."""
    from debrief.app.maps import PROGRESS_TIME, progress_thresholds, segment_loss

    soft, hard, _ = progress_thresholds(PROGRESS_TIME)
    subject = next(p for p in progress.pilots if not p.is_reference)
    worst = subject.ranked_segments(worst_first=True)[0]
    assert segment_loss(worst, PROGRESS_TIME) == worst.lost_s
    assert LIGHT.diverging.color(worst.lost_s, soft, hard) == LIGHT.diverging.loss_strong
    assert LIGHT.diverging.color(-worst.lost_s, soft, hard) == LIGHT.diverging.gain_strong


def test_height_flips_sign_so_that_lower_reads_as_worse(progress):
    """Being below the reference is the bad one. A scale whose red arm meant
    'higher' would send a pilot exactly the wrong message."""
    from debrief.app.maps import PROGRESS_HEIGHT, segment_loss

    subject = next(p for p in progress.pilots if not p.is_reference)
    measured = [s for s in subject.segments if s.height_delta_m is not None]
    assert measured
    for segment in measured[:20]:
        assert segment_loss(segment, PROGRESS_HEIGHT) == -segment.height_delta_m


def test_thicker_track_means_a_bigger_difference(progress_spec):
    """Colour alone cannot carry magnitude: under colour-blind simulation the
    red arm sits closer to the neutral than the blue one does."""
    widths = [
        datum["width"]
        for layer in progress_spec["layers"]
        for datum in (layer["data"] if isinstance(layer.get("data"), list) else [])
        if "width" in datum
    ]
    assert len(set(widths)) > 1


def test_progress_tooltips_are_plain_text(progress_spec):
    """Streamlit renders a deck tooltip as text, so markup arrives as literal
    angle brackets."""
    for layer in progress_spec["layers"]:
        for datum in layer["data"] if isinstance(layer.get("data"), list) else []:
            assert "<" not in datum.get("info", "")


def test_the_thresholds_differ_by_what_is_being_measured():
    from debrief.app.maps import PROGRESS_HEIGHT, PROGRESS_TIME, progress_thresholds

    seconds = progress_thresholds(PROGRESS_TIME)
    metres = progress_thresholds(PROGRESS_HEIGHT)
    assert seconds[0] < seconds[1] and metres[0] < metres[1]
    assert seconds[2].strip() == "s"
    assert metres[2].strip() == "m"


def test_a_time_threshold_scales_with_the_stretch_it_judges():
    """Fixed thresholds would paint a 25 km stretch red from end to end and
    leave a 2 km one permanently in the neutral band — the same flying, two
    opposite readings, decided by a slider."""
    from debrief.app.maps import PROGRESS_HEIGHT, PROGRESS_TIME, progress_thresholds

    fine = progress_thresholds(PROGRESS_TIME, 2000.0)
    coarse = progress_thresholds(PROGRESS_TIME, 25000.0)
    assert coarse[0] / fine[0] == pytest.approx(25000.0 / 2000.0)
    assert coarse[1] / fine[1] == pytest.approx(25000.0 / 2000.0)
    # Height is a state, not a rate, so it does not scale.
    assert progress_thresholds(PROGRESS_HEIGHT, 2000.0) == progress_thresholds(PROGRESS_HEIGHT, 25000.0)


def test_the_progress_map_never_mixes_identity_with_polarity(progress_spec):
    """The diverging scale spends the colour channel on which side of the
    reference a pilot was. Any pilot-identity colour on the same map would be
    read as a position on that scale."""
    scale = LIGHT.diverging
    # Alpha varies between the track and its markers; the hue is the message.
    allowed = {
        tuple(hex_to_rgb(colour)[:3])
        for colour in (scale.gain_strong, scale.gain, scale.neutral, scale.loss, scale.loss_strong)
    }
    coloured = [
        tuple(datum["color"][:3])
        for layer in progress_spec["layers"]
        for datum in (layer["data"] if isinstance(layer.get("data"), list) else [])
        if "color" in datum
    ]
    assert coloured
    assert set(coloured) <= allowed
