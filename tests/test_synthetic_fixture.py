"""The fixture generator is load-bearing, so it gets its own tests.

If these fail, every other failure in the suite is suspect.
"""

from aerofiles.igc import Reader

from tests.fixtures.synthetic import build_igc, format_lat, format_lon


def test_b_records_are_35_characters():
    lines = build_igc().splitlines()
    b_records = [line for line in lines if line.startswith("B")]
    assert b_records, "fixture produced no B records"
    assert all(len(record) == 35 for record in b_records)


def test_coordinate_formatting_round_trips():
    # 51.2917 N -> 51 deg 17.502 min
    assert format_lat(51.2917) == "5117502N"
    assert format_lon(14.5167) == "01431002E"
    assert format_lat(-33.5) == "3330000S"
    assert format_lon(-70.25) == "07015000W"


def test_minute_rounding_carries():
    """0.99999 deg is 59.9994 min, which must not format as minute 60."""
    formatted = format_lat(0.999999)
    assert formatted == "0100000N", formatted


def test_aerofiles_parses_the_fixture_without_errors(synthetic_igc):
    with open(synthetic_igc) as handle:
        parsed = Reader(skip_duplicates=True).read(handle)
    errors, trace = parsed["fix_records"]
    assert errors == []
    assert len(trace) > 1000
    assert {"datetime", "lat", "lon", "gps_alt", "pressure_alt"} <= set(trace[0])
