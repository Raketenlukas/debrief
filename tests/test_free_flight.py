"""Flights with no declared task.

A file without a task declaration is still worth debriefing: thermals, climb
rates, circling share, cruise speed and achieved glide do not depend on a task.
Only the task-shaped numbers are absent, and they are absent rather than faked.
"""

import pytest

from debrief.core.igc import load_igc
from debrief.core.metrics import analyse, analyse_or_summarise, summarise


@pytest.fixture(scope="module")
def free_flight_igc(tmp_path_factory, synthetic_igc):
    """The synthetic flight with every task declaration stripped out."""
    path = tmp_path_factory.mktemp("igc") / "free.igc"
    kept = [
        line
        for line in synthetic_igc.read_text().splitlines(True)
        if not line.startswith(("C", "LCU::", "LSEEYOU", "LNAVOZN"))
    ]
    path.write_text("".join(kept))
    return path


@pytest.fixture(scope="module")
def free(free_flight_igc):
    return summarise(load_igc(free_flight_igc))


def test_a_file_without_a_task_still_loads(free_flight_igc):
    assert load_igc(free_flight_igc).task is None


def test_analyse_still_refuses_without_a_task(free_flight_igc):
    """The strict entry point keeps its contract; the lenient one is separate."""
    with pytest.raises(ValueError, match="no declared task"):
        analyse(load_igc(free_flight_igc))


def test_analyse_or_summarise_opens_it(free_flight_igc):
    metrics = analyse_or_summarise(load_igc(free_flight_igc))
    assert not metrics.has_task
    assert metrics.legs == ()


def test_task_free_metrics_are_real_numbers(free):
    assert free.thermal_count > 3
    assert 0.5 < free.average_climb_ms < 8
    assert 0 < free.percent_circling < 80
    assert free.distance_flown_km > 50
    assert free.cruise_speed_kmh > 20
    assert free.glide_ratio > 10
    assert free.overall.altitude_min < free.overall.altitude_max


def test_task_shaped_values_are_absent_not_zero(free):
    """Reporting 0 km/h task speed for a free flight would be a lie."""
    assert free.task is None
    assert free.task_key is None
    assert free.final_glide_km is None
    assert free.final_glide_height is None
    assert not free.completed
    assert not free.outlanded


def test_duration_covers_the_whole_flight(free):
    trace = free.flight.trace
    expected = (trace[-1]["datetime"] - trace[0]["datetime"]).total_seconds()
    assert free.task_duration_s == pytest.approx(expected)
    assert free.start_time == trace[0]["datetime"]
    assert free.finish_time == trace[-1]["datetime"]


def test_task_flight_is_unaffected_by_the_fallback(synthetic_igc):
    """The whole-flight aggregate must not leak into leg-based flights."""
    metrics = analyse(load_igc(synthetic_igc))
    assert metrics.overall is None
    assert metrics.has_task
    assert metrics.legs


def test_summarise_refuses_a_trace_too_short_to_measure(synthetic_igc, tmp_path):
    flight = load_igc(synthetic_igc)
    flight.trace = flight.trace[:1]
    with pytest.raises(ValueError, match="too short"):
        summarise(flight)
