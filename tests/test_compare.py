"""Comparing several flights over one task."""

import datetime as dt

import pytest

from debrief.core.compare import DayComparison, Distribution
from debrief.core.metrics import analyse_or_summarise
from debrief.sources.local import archived_days
from debrief.sources.soaringspot import SoaringSpotDay
from tests.fixtures.fake_soaringspot import Competitor, FakeSoaringSpot

FIELD = (
    Competitor("1", "AA", "Fast Pilot", "Ventus 3", 34.5, 2.5),
    Competitor("2", "BB", "Middle Pilot", "ASG 29", 31.0, 2.0),
    Competitor("3", "CC", "Slow Pilot", "LS8", 28.0, 1.7),
)


@pytest.fixture(scope="module")
def day(tmp_path_factory):
    root = tmp_path_factory.mktemp("archive")
    with FakeSoaringSpot(competitors=FIELD) as server:
        flights = list(SoaringSpotDay(server.daily_url(), root).flights())
    return DayComparison.build([analyse_or_summarise(f) for f in flights])


def test_flights_are_ordered_fastest_first(day):
    speeds = [f.task_speed_kmh for f in day.flights]
    assert speeds == sorted(speeds, reverse=True)
    assert day.flights[0].flight.pilot.competition_id == "AA"


def test_the_fastest_pilot_is_the_default_reference(day):
    assert day.reference.flight.pilot.competition_id == "AA"


def test_everyone_flew_the_same_task(day):
    assert len({f.task_key for f in day.flights}) == 1
    assert day.task_label


def test_leg_deltas_are_zero_against_yourself(day):
    reference = day.comparison_for(day.reference)
    assert all(leg.delta_s == 0 for leg in reference.legs)
    assert reference.total_delta_s == 0


def test_slower_pilots_lose_time_on_every_leg(day):
    """The simulated gliders are uniformly slower, so no leg should be a gain."""
    for comparison in day.comparisons():
        if comparison.metrics is day.reference:
            continue
        deltas = [leg.delta_s for leg in comparison.legs if leg.delta_s is not None]
        assert deltas and all(d > 0 for d in deltas), comparison.label
        assert comparison.total_delta_s == pytest.approx(sum(deltas))


def test_total_delta_grows_with_rank(day):
    totals = [c.total_delta_s for c in day.comparisons() if c.metrics is not day.reference]
    assert totals == sorted(totals)


def test_cumulative_deltas_are_a_running_total(day):
    comparison = [c for c in day.comparisons() if c.metrics is not day.reference][0]
    running = comparison.cumulative_deltas()
    assert running[-1] == pytest.approx(comparison.total_delta_s)
    assert running == sorted(running), "a uniformly slower pilot never claws time back"


def test_choosing_a_different_reference_flips_the_sign(day):
    slowest = day.flights[-1]
    rebuilt = DayComparison.build(list(day.flights), reference_key=slowest.flight.flight_key)
    fastest = rebuilt.comparison_for(rebuilt.flights[0])
    assert fastest.total_delta_s < 0, "the fastest pilot gains time on the slowest"


def test_distribution_ranks_and_percentiles(day):
    speed = day.distribution("task_speed_kmh", higher_is_better=True)
    assert speed.best == max(speed.values)
    assert speed.rank_of(speed.best) == 1
    assert speed.percentile_of(speed.best) == 100
    assert speed.percentile_of(min(speed.values)) == 0


def test_lower_is_better_metrics_rank_the_other_way():
    """Detour is a metric where less is better; the ranking must follow."""
    detour = Distribution((2.0, 5.0, 9.0), higher_is_better=False)
    assert detour.best == 2.0
    assert detour.rank_of(2.0) == 1
    assert detour.percentile_of(2.0) == 100
    assert detour.median == 5.0


def test_distribution_handles_a_single_flight():
    lonely = Distribution((3.0,), higher_is_better=True)
    assert lonely.best == 3.0
    assert lonely.rank_of(3.0) == 1
    assert lonely.percentile_of(3.0) is None, "one flight is not a field"


def test_flights_on_a_different_task_are_dropped(day, tmp_path):
    """Comparing across tasks is meaningless, so it is refused rather than shown.

    Note a different *date* is not a different task: the task key comes from
    turnpoint geometry, so the same triangle flown on two days is one task. It
    takes different geometry to split them.
    """
    from debrief.core.igc import load_igc
    from tests.fixtures.synthetic import SHORT_FINAL_LEG_TASK, write_igc

    other_path = tmp_path / "other-task.igc"
    write_igc(other_path, task=SHORT_FINAL_LEG_TASK)
    other = analyse_or_summarise(load_igc(other_path))
    assert other.task_key != day.flights[0].task_key

    rebuilt = DayComparison.build([*day.flights, other])
    assert len({f.task_key for f in rebuilt.flights}) == 1
    assert any("different task" in w for w in rebuilt.warnings)
    # The majority task is the one kept.
    assert rebuilt.flights[0].task_key == day.flights[0].task_key


def test_the_same_task_on_another_day_is_still_the_same_task(day, tmp_path):
    """Deliberate: a task is its geometry. Days are separated by the archive
    layout, not by pretending identical triangles are different tasks."""
    with FakeSoaringSpot(competitors=FIELD[:1], date=dt.date(2024, 7, 1)) as server:
        other = list(SoaringSpotDay(server.daily_url(), tmp_path).flights())
    assert analyse_or_summarise(other[0]).task_key == day.flights[0].task_key


def test_flights_without_a_task_are_dropped(day, synthetic_igc, tmp_path):
    from debrief.core.igc import load_igc

    stripped = tmp_path / "notask.igc"
    stripped.write_text(
        "".join(
            line
            for line in synthetic_igc.read_text().splitlines(True)
            if not line.startswith(("C", "LCU::", "LSEEYOU"))
        )
    )
    mixed = list(day.flights) + [analyse_or_summarise(load_igc(stripped))]

    rebuilt = DayComparison.build(mixed)
    assert all(f.has_task for f in rebuilt.flights)
    assert any("without a task" in w for w in rebuilt.warnings)


def test_an_empty_comparison_is_not_an_error():
    empty = DayComparison.build([])
    assert empty.flights == ()
    assert empty.reference is None
    assert empty.task_label is None


def test_elapsed_seconds_aligns_on_each_pilots_own_start(day):
    """The point of start-alignment: two pilots twenty minutes apart line up."""
    for metrics in day.flights:
        assert day.elapsed_seconds(metrics, metrics.start_time) == 0.0
        later = metrics.start_time + dt.timedelta(minutes=30)
        assert day.elapsed_seconds(metrics, later) == 1800.0


def test_archived_days_round_trip(tmp_path):
    with FakeSoaringSpot(competitors=FIELD) as server:
        SoaringSpotDay(server.daily_url(competition="comp-a", plane_class="club"), tmp_path).download()
        SoaringSpotDay(server.daily_url(competition="comp-a", plane_class="18m"), tmp_path).download()

    days = archived_days(tmp_path)
    assert len(days) == 2
    assert {d.plane_class for d in days} == {"club", "18m"}
    assert all(len(d.paths) == len(FIELD) for d in days)
    assert all(d.competition == "comp-a" for d in days)


def test_directories_that_are_not_days_are_ignored(tmp_path):
    (tmp_path / "loose").mkdir()
    (tmp_path / "loose" / "flight.igc").write_text("not really")
    (tmp_path / "comp" / "class" / "not-a-date").mkdir(parents=True)
    (tmp_path / "comp" / "class" / "not-a-date" / "x.igc").write_text("nope")
    assert archived_days(tmp_path) == []
