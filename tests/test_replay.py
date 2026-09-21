"""Replay payload.

The browser half is verified by driving it; this covers the data crossing into
it, which is where a silent mistake would be hardest to see — a wrong timeline
or a dropped track just looks like a flight that sat still.
"""

import json

import pytest

from debrief.app.replay import TARGET_SAMPLES, payload, payload_json
from debrief.app.theme import LIGHT
from debrief.core.igc import load_igc
from debrief.core.metrics import analyse_or_summarise


@pytest.fixture(scope="module")
def flights(synthetic_igc, second_pilot_igc):
    return [analyse_or_summarise(load_igc(p)) for p in (synthetic_igc, second_pilot_igc)]


@pytest.fixture(scope="module")
def clock_payload(flights):
    return payload(flights, list(LIGHT.series), align="clock")


def test_every_flight_becomes_a_track(clock_payload, flights):
    assert len(clock_payload["tracks"]) == len(flights)
    assert [t["label"] for t in clock_payload["tracks"]] == [f.flight.pilot.label for f in flights]


def test_tracks_get_distinct_colours(clock_payload):
    colours = [t["color"] for t in clock_payload["tracks"]]
    assert len(set(colours)) == len(colours)


def test_arrays_within_a_track_stay_the_same_length(clock_payload):
    """A short array would silently desynchronise position from altitude."""
    for track in clock_payload["tracks"]:
        n = len(track["t"])
        assert n > 10
        assert len(track["lon"]) == n
        assert len(track["lat"]) == n
        assert len(track["alt"]) == n


def test_time_is_monotonic(clock_payload):
    for track in clock_payload["tracks"]:
        assert track["t"] == sorted(track["t"])


def test_thinning_respects_the_target_and_keeps_the_ends(flights):
    small = payload(flights, list(LIGHT.series), target_samples=200)
    for index, track in enumerate(small["tracks"]):
        assert len(track["t"]) <= 260, "thinning overshot its target"
        trace = flights[index].flight.trace
        assert track["t"][0] == round(trace[0]["datetime"].timestamp())
        assert track["t"][-1] == round(trace[-1]["datetime"].timestamp())


def test_clock_alignment_puts_pilots_on_one_timeline(clock_payload, flights):
    """They flew at the same time, so their spans must overlap."""
    starts = [t["t"][0] for t in clock_payload["tracks"]]
    ends = [t["t"][-1] for t in clock_payload["tracks"]]
    assert max(starts) < min(ends), "clock-aligned flights should overlap in time"
    assert clock_payload["t0"] == min(starts)
    assert clock_payload["t1"] == max(ends)


def test_start_alignment_measures_from_each_pilots_own_start(flights):
    """The point of it: two pilots that started apart line up at zero."""
    aligned = payload(flights, list(LIGHT.series), align="start")
    for index, track in enumerate(aligned["tracks"]):
        start = flights[index].start_time
        trace = flights[index].flight.trace
        expected_first = round((trace[0]["datetime"] - start).total_seconds())
        assert track["t"][0] == expected_first
        # Pre-start time is negative; the task itself begins at zero.
        assert track["t"][0] < 0 < track["t"][-1]


def test_alignment_changes_the_timeline_not_the_geometry(flights):
    clock = payload(flights, list(LIGHT.series), align="clock")
    start = payload(flights, list(LIGHT.series), align="start")
    for a, b in zip(clock["tracks"], start["tracks"], strict=True):
        assert a["lon"] == b["lon"]
        assert a["lat"] == b["lat"]
        assert a["alt"] == b["alt"]
        assert a["t"] != b["t"]


def test_bounds_cover_every_track(clock_payload):
    (west, south), (east, north) = clock_payload["bounds"]
    for track in clock_payload["tracks"]:
        assert west <= min(track["lon"]) and max(track["lon"]) <= east
        assert south <= min(track["lat"]) and max(track["lat"]) <= north


def test_coordinates_are_rounded_but_still_precise(clock_payload):
    """Five decimals is about a metre — far finer than a glider's position
    means, and it roughly halves the payload."""
    for track in clock_payload["tracks"]:
        for lon in track["lon"][:50]:
            assert round(lon, 5) == lon
        assert all(isinstance(a, int) for a in track["alt"][:50])


def test_payload_json_is_valid_and_cannot_break_out_of_a_script_block():
    """The page embeds this inline, so a literal </script> would end it early."""
    hostile = {"tracks": [], "t0": 0, "t1": 1, "align": "clock", "label": "</script><b>x"}
    text = payload_json(hostile)
    assert "</script>" not in text
    assert "<\\/script>" in text
    assert json.loads(text.replace("<\\/", "</"))["label"] == "</script><b>x"


def test_no_flights_is_not_an_error():
    empty = payload([], list(LIGHT.series))
    assert empty["tracks"] == []
    assert empty["t0"] == empty["t1"] == 0


def test_a_flight_too_short_to_replay_is_dropped(flights):
    short = flights[0]
    short.flight.trace = short.flight.trace[:1]
    assert payload([short], list(LIGHT.series))["tracks"] == []


def test_default_target_is_sane():
    assert 500 <= TARGET_SAMPLES <= 5000


# --- the page itself ---------------------------------------------------------
#
# The browser half is verified by driving it, which needs a running app. These
# guard the two things that broke silently: a control disappearing from the
# toolbar, and a placeholder the renderer forgets to substitute (a leftover
# ``__TILES__`` is a JavaScript syntax error, so the whole replay goes blank).


@pytest.fixture(scope="module")
def page_template():
    from pathlib import Path

    import debrief.app.replay as replay_module

    return (Path(replay_module.__file__).parent / "replay_page.html").read_text(encoding="utf-8")


def test_the_toolbar_carries_every_control_the_renderer_expects(page_template):
    for control in ("play", "dir", "speed", "home", "fit", "zoomin", "zoomout", "clock"):
        assert f'id="{control}"' in page_template, control


def test_zooming_is_reachable_by_button_wheel_and_double_click(page_template):
    """A mouse, a trackpad and a touchpad user each reach for a different one."""
    assert 'getElementById("zoomin").onclick' in page_template
    assert 'getElementById("zoomout").onclick' in page_template
    assert 'addEventListener("wheel"' in page_template
    assert 'addEventListener("dblclick"' in page_template


def test_every_placeholder_in_the_page_is_one_the_renderer_substitutes(page_template):
    """Renaming a placeholder in the template without touching render() leaves a
    bare ``__NAME__`` in the emitted script."""
    import re

    substituted = {
        "__PAYLOAD__",
        "__PALETTE__",
        "__SURFACE__",
        "__INK__",
        "__MUTED__",
        "__GRID__",
        "__AXIS__",
        "__TILES__",
        "__ATTRIBUTION__",
    }
    assert set(re.findall(r"__[A-Z_]+__", page_template)) == substituted
