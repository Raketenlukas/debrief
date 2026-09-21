"""The whole field's day: the lift map, the route map, and the composite.

The composite carries the invariant worth protecting most here. Because every
single pilot's own flight is a legal path through the search, the composite can
never be slower than the quickest of them — so if it ever is, the search has
silently dropped that pilot, and the number on screen is not a ceiling but an
arithmetic accident. It has been one twice already: once from starting the span
at zero, where the later-crossing tracks (often the quickest) had no data yet,
and once from rounding the carried height into buckets, which leaked away the
few tens of metres that were the composite's margin over the pilot it was
copying.
"""

import datetime as dt

import pytest

from debrief.core.field import (
    CLIMB_MIN_GAIN_M,
    CLIMB_MIN_SECONDS,
    HEIGHT_TOLERANCE_M,
    CompositeBest,
    climb_grid,
    cruise_grid,
    field_climbs,
    field_cruise,
    grid,
)
from debrief.core.igc import load_igc
from debrief.core.metrics import analyse_or_summarise
from debrief.core.progress import GEOD
from tests.fixtures.synthetic import DEFAULT_TASK, write_igc

FIELD = [
    ("7L", "Anna Beispiel", "Ventus 3", 34.0, 2.4, dt.time(11, 30)),
    ("XY", "Beat Muster", "ASG 29", 29.0, 1.8, dt.time(11, 30)),
    ("LT", "Carla Spaet", "JS3", 33.0, 2.2, dt.time(12, 10)),
    ("MM", "Dario Rossi", "Ventus 3", 31.5, 2.0, dt.time(11, 45)),
    ("QQ", "Elena Novak", "ASG 32", 32.5, 2.6, dt.time(11, 35)),
]


@pytest.fixture(scope="module")
def field(tmp_path_factory):
    """A class flying one task — the input every one of these views needs."""
    root = tmp_path_factory.mktemp("field")
    out = []
    for cn, pilot, glider, cruise, climb, start in FIELD:
        path = root / f"{cn}.igc"
        write_igc(
            path,
            task=DEFAULT_TASK,
            pilot=pilot,
            glider_model=glider,
            glider_id=f"D-{cn}",
            competition_id=cn,
            cruise_speed=cruise,
            climb_rate=climb,
            start_time=start,
        )
        out.append(analyse_or_summarise(load_igc(path)))
    return out


# --- the lift map ------------------------------------------------------------


def test_every_worthwhile_climb_in_the_field_is_found(field):
    climbs = field_climbs(field)
    assert climbs
    assert {c.pilot for c in climbs} == {m.flight.pilot.label for m in field}
    assert all(c.climb_ms > 0 for c in climbs)


def test_wriggles_are_not_climbs(field):
    """The phase detector segments on turn rate, so a bump taken in a turn
    comes back as a climb. By number those would swamp the map."""
    kept = field_climbs(field)
    assert all(c.height_gain >= CLIMB_MIN_GAIN_M for c in kept)
    assert all(c.duration_s >= CLIMB_MIN_SECONDS for c in kept)
    assert len(field_climbs(field, min_gain_m=0.0, min_seconds=0.0)) >= len(kept)


def test_a_climb_is_placed_where_it_was_worked_not_where_it_was_entered(field):
    """A thermal drifts downwind while it is being climbed; the entry point is
    where the glider arrived, not where the lift was."""
    for metrics in field:
        trace = metrics.flight.trace
        for climb in field_climbs([metrics]):
            fixes = [f for f in trace if climb.start_time <= f["datetime"] <= climb.end_time]
            assert min(f["lat"] for f in fixes) <= climb.latitude <= max(f["lat"] for f in fixes)
            assert min(f["lon"] for f in fixes) <= climb.longitude <= max(f["lon"] for f in fixes)


def test_a_climb_carries_how_far_the_thermal_drifted(field):
    climbs = field_climbs(field)
    assert all(c.drift_m >= 0 for c in climbs)
    assert any(c.drift_m > 0 for c in climbs)


# --- the route map -----------------------------------------------------------


def test_the_route_map_leaves_the_circling_out(field):
    """Climbing is where a glider stops. Left in, dozens of fixes pile onto one
    spot and a map of routes becomes a map of thermals, which is the other map."""
    points = field_cruise(field)
    assert points
    for metrics in field:
        spans = [(t.start_time, t.end_time) for t in metrics.thermals]
        mine = [p for p in points if p.pilot == metrics.flight.pilot.label]
        assert mine
        assert not any(start <= p.time <= end for p in mine for start, end in spans)


def test_the_route_map_is_thinned(field):
    fixes = sum(len(m.flight.trace) for m in field)
    assert len(field_cruise(field, sample_seconds=30.0)) < fixes


# --- the grid ----------------------------------------------------------------


def test_cells_are_the_size_they_claim_to_be():
    """Binned in degrees, a "5 km" cell would be a different size at every
    competition site, and two sites could not be compared at all."""
    points = [(46.0 + 0.01 * i, 8.0 + 0.01 * j, 1.0, "p") for i in range(12) for j in range(12)]
    for cell_m in (2500.0, 5000.0, 10000.0):
        cells = grid(points, cell_m=cell_m)
        assert cells
        for cell in cells[:6]:
            (lon0, lat0), (lon1, lat1) = cell.polygon[0], cell.polygon[1]
            width = GEOD.inv(lon0, lat0, lon1, lat1)[2]
            (lon2, lat2) = cell.polygon[2]
            height = GEOD.inv(lon1, lat1, lon2, lat2)[2]
            assert width == pytest.approx(cell_m, rel=0.01)
            assert height == pytest.approx(cell_m, rel=0.01)


def test_every_point_lands_in_exactly_one_cell():
    points = [(46.0 + 0.013 * i, 8.0 + 0.017 * j, float(i + j), "p") for i in range(9) for j in range(9)]
    cells = grid(points, cell_m=5000.0)
    assert sum(c.count for c in cells) == len(points)


def test_a_coarser_grid_has_fewer_cells():
    points = [(46.0 + 0.01 * i, 8.0 + 0.01 * j, 1.0, "p") for i in range(15) for j in range(15)]
    assert len(grid(points, cell_m=10000.0)) < len(grid(points, cell_m=2500.0))


def test_a_cell_counts_pilots_separately_from_samples():
    """One pilot circling is not a hot spot. The map shades by how many people
    found something, so the two counts must not be conflated."""
    here = [(46.0, 8.0, 2.0, "A")] * 9 + [(46.0, 8.0, 3.0, "B")]
    cell = grid(here, cell_m=5000.0)[0]
    assert cell.count == 10
    assert cell.pilot_count == 2


def test_a_cell_summarises_what_landed_in_it():
    cell = grid([(46.0, 8.0, v, "p") for v in (1.0, 2.0, 6.0)], cell_m=5000.0)[0]
    assert cell.mean == pytest.approx(3.0)
    assert cell.median == pytest.approx(2.0)
    assert cell.best == pytest.approx(6.0)


def test_an_empty_field_grids_to_nothing():
    assert grid([]) == ()


def test_the_two_grids_read_the_values_their_maps_need(field):
    climbs = list(field_climbs(field))
    lift = climb_grid(climbs, cell_m=5000.0)
    assert lift
    assert all(min(c.values) > 0 for c in lift)  # climb rates
    routes = cruise_grid(list(field_cruise(field)), cell_m=5000.0)
    assert routes
    assert max(max(c.values) for c in routes) > 500  # altitudes, in metres


def test_a_minimum_count_drops_the_thin_cells(field):
    climbs = list(field_climbs(field))
    every = climb_grid(climbs, cell_m=5000.0)
    busy = climb_grid(climbs, cell_m=5000.0, min_count=3)
    assert len(busy) < len(every)
    assert all(c.count >= 3 for c in busy)


# --- the composite -----------------------------------------------------------


def test_the_composite_is_never_slower_than_the_best_single_pilot(field):
    """The invariant. Every pilot's own flight is a legal path through the
    search, so the answer is bounded by the quickest of them. A composite that
    comes out slower has dropped somebody, and is not a ceiling at all."""
    for segment_m in (2000.0, 5000.0, 10000.0, 25000.0):
        composite = CompositeBest.build(field, segment_m=segment_m)
        assert composite is not None
        assert composite.winner_seconds is not None
        assert composite.seconds <= composite.winner_seconds + 1e-6, segment_m
        assert composite.gain_on_winner_s >= -1e-6, segment_m


def test_the_composite_never_borrows_a_glide_that_started_above_it(field):
    """The energy constraint, checked on the answer rather than trusted in the
    search. Without it the composite takes everyone's glides and nobody's
    climbs — a glider that flies the whole task without stopping."""
    composite = CompositeBest.build(field, segment_m=10000.0)
    assert len(composite.altitudes) == len(composite.stretches) + 1
    for carried, stretch in zip(composite.altitudes, composite.stretches, strict=False):
        assert carried + HEIGHT_TOLERANCE_M >= stretch.entry_altitude


def test_the_composite_stays_at_the_height_its_contributor_flew(field):
    """It follows from the rule above, and it is what keeps the composite from
    being somewhere no glider demonstrated was survivable.

    Within the tolerance, which is the only energy the composite is ever lent:
    it may enter a stretch up to that far below its contributor, so it may
    leave up to that far below them too. Nothing compounds — each stretch is
    measured against its own contributor, not against the last one.
    """
    composite = CompositeBest.build(field, segment_m=10000.0)
    for arrival, stretch in zip(composite.altitudes[1:], composite.stretches, strict=False):
        assert arrival >= stretch.exit_altitude - HEIGHT_TOLERANCE_M


def test_finer_pieces_never_make_a_slower_composite(field):
    """Every coarse piece is expressible as a run of fine ones, so more choice
    cannot cost time. If it does, the search is losing something between
    stages."""
    times = [CompositeBest.build(field, segment_m=m).seconds for m in (2000.0, 10000.0, 25000.0)]
    assert times[0] <= times[1] + 1e-6
    assert times[1] <= times[2] + 1e-6


def test_the_composite_reaches_the_end_of_the_course(field):
    composite = CompositeBest.build(field, segment_m=10000.0)
    assert composite.complete
    assert composite.stretches[0].start_m < composite.stretches[-1].end_m


def test_a_coarse_enough_composite_is_just_the_winners_flight(field):
    """A sanity check on the whole construction: with pieces long enough that
    mixing buys nothing, the best assembly of the day is somebody's actual
    flight."""
    composite = CompositeBest.build(field, segment_m=25000.0)
    pilot, _, share = composite.shares[0]
    assert share > 90.0
    assert pilot == composite.winner_label


def test_shares_account_for_the_whole_task(field):
    composite = CompositeBest.build(field, segment_m=5000.0)
    assert sum(share for _, _, share in composite.shares) == pytest.approx(100.0)
    assert sum(count for _, count, _ in composite.shares) == len(composite.stretches)
    assert composite.shares == sorted(composite.shares, key=lambda row: row[2], reverse=True)


def test_each_stretch_carries_who_flew_it_and_in_what(field):
    """The composite is not flyable and the provenance is how a reader knows
    that, so it is not optional decoration."""
    composite = CompositeBest.build(field, segment_m=10000.0)
    for stretch in composite.stretches:
        assert stretch.pilot
        assert stretch.glider
        assert stretch.speed_kmh > 0
        assert len(stretch.path) >= 2


def test_a_stretch_is_drawn_along_the_track_that_was_flown(field):
    """Not the straight line between its ends: a stretch with a climb in it has
    to show the circling, or the map claims a flight nobody made."""
    composite = CompositeBest.build(field, segment_m=10000.0)
    longest = max(composite.stretches, key=lambda s: s.seconds)
    flown = sum(
        GEOD.inv(a[0], a[1], b[0], b[1])[2] for a, b in zip(longest.path, longest.path[1:], strict=False)
    )
    assert flown > longest.distance_m


def test_one_flight_is_not_a_composite(field):
    assert CompositeBest.build(field[:1]) is None


def test_a_task_shorter_than_one_stretch_is_not_a_composite(field):
    assert CompositeBest.build(field, segment_m=10_000_000.0) is None
