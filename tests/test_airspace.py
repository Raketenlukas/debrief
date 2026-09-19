"""OpenAIR parsing tests.

Geometry is checked against the geodesic distances the file declares, not just
for plausibility: an arc or circle that is merely present but the wrong size
draws a convincing picture of the wrong airspace.
"""

import math
from pathlib import Path

import pytest
from pyproj import Geod

from debrief.core.airspace import METRES_PER_NM, load_openair, parse_altitude

GEOD = Geod(ellps="WGS84")
SAMPLE = Path(__file__).parent / "fixtures" / "sample_airspace.txt"


@pytest.fixture(scope="module")
def airspaces():
    return {a.name: a for a in load_openair(SAMPLE)}


def test_every_record_is_parsed(airspaces):
    assert set(airspaces) == {
        "RIETI CTR",
        "ROMA TMA SECTOR 3",
        "LI R60 TERNI",
        "DANGER AREA ARC",
        "WAVE WINDOW",
    }


def test_rings_are_closed_and_in_lon_lat_order(airspaces):
    for airspace in airspaces.values():
        for ring in airspace.rings:
            assert len(ring) >= 4
            assert ring[0] == ring[-1], f"{airspace.name} ring not closed"
            for lon, lat in ring:
                # Central Italy: longitude ~11-13, latitude ~41-43. Swapping the
                # pair still yields a valid-looking coordinate, so check the range.
                assert 5 < lon < 20, f"{airspace.name}: lon/lat likely swapped"
                assert 35 < lat < 50, f"{airspace.name}: lon/lat likely swapped"


def test_circle_has_the_declared_radius(airspaces):
    """DC 5 is 5 nautical miles, not 5 of anything else."""
    ring = airspaces["LI R60 TERNI"].rings[0]
    centre_lat, centre_lon = 42 + 34 / 60, 12 + 39 / 60
    distances = [GEOD.inv(centre_lon, centre_lat, lon, lat)[2] for lon, lat in ring]
    assert min(distances) == pytest.approx(5 * METRES_PER_NM, rel=1e-6)
    assert max(distances) == pytest.approx(5 * METRES_PER_NM, rel=1e-6)


def test_arc_spans_the_declared_bearings(airspaces):
    """DA 10,0,90 is a quarter circle from north to east, clockwise."""
    ring = airspaces["DANGER AREA ARC"].rings[0]
    centre_lat, centre_lon = 42 + 45 / 60, 12 + 20 / 60
    on_arc = [
        (lon, lat)
        for lon, lat in ring
        if abs(GEOD.inv(centre_lon, centre_lat, lon, lat)[2] - 10 * METRES_PER_NM) < 1.0
    ]
    assert len(on_arc) > 5
    bearings = sorted(GEOD.inv(centre_lon, centre_lat, lon, lat)[0] % 360.0 for lon, lat in on_arc)
    assert bearings[0] == pytest.approx(0.0, abs=0.5)
    assert bearings[-1] == pytest.approx(90.0, abs=0.5)


def test_first_polygon_vertex_matches_the_file(airspaces):
    lon, lat = airspaces["RIETI CTR"].rings[0][0]
    assert lat == pytest.approx(42 + 25 / 60 + 33 / 3600)
    assert lon == pytest.approx(12 + 56 / 60 + 45 / 3600)


@pytest.mark.parametrize(
    ("text", "feet", "datum"),
    [
        ("GND", 0.0, "agl"),
        ("SFC", 0.0, "agl"),
        ("UNLIM", None, "unlimited"),
        ("FL195", 19500.0, "msl"),
        ("FL 65", 6500.0, "msl"),
        ("2000ft AMSL", 2000.0, "msl"),
        ("3500ft MSL", 3500.0, "msl"),
        ("1000ft AGL", 1000.0, "agl"),
        ("4500", 4500.0, "msl"),
        ("1000m MSL", 3280.839895, "msl"),
    ],
)
def test_altitude_parsing(text, feet, datum):
    altitude = parse_altitude(text)
    assert altitude.datum == datum
    if feet is None:
        assert altitude.feet is None
    else:
        assert altitude.feet == pytest.approx(feet, rel=1e-6)
    assert altitude.text == text


def test_unparseable_altitude_falls_back_to_unlimited():
    """Unknown spellings must not crash a national file mid-parse."""
    assert parse_altitude("BY NOTAM").datum == "unlimited"
    assert parse_altitude("").datum == "unlimited"
    assert parse_altitude(None).datum == "unlimited"


def test_band_intersection(airspaces):
    tma = airspaces["ROMA TMA SECTOR 3"]  # 3500 ft -> FL195
    assert not tma.intersects_band(2000, 3000)
    assert tma.intersects_band(3000, 9000)
    assert tma.intersects_band(3500, 3500)
    assert not tma.intersects_band(20000, 25000)


def test_agl_floor_is_treated_as_reaching_the_ground(airspaces):
    """Without a terrain model an AGL limit cannot be resolved, so the filter
    errs toward showing the airspace rather than hiding it."""
    danger = airspaces["DANGER AREA ARC"]  # 1000 ft AGL -> UNLIM
    assert danger.floor.datum == "agl"
    assert danger.intersects_band(0, 500)
    assert danger.ceiling.is_unlimited
    assert danger.intersects_band(30000, 40000)


def test_unlimited_ceiling_is_infinite_not_zero(airspaces):
    assert airspaces["DANGER AREA ARC"].intersects_band(0, math.inf)


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_openair(tmp_path / "nope.txt")
