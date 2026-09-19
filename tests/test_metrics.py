"""Metric tests.

The synthetic glider has a known polar — it cruises at 33 m/s sinking 1.15 m/s,
so its achieved glide ratio is 33/1.15 = 28.7 — and climbs at a known rate. That
makes the fixture a ground truth: if the analysis says something far from those
numbers, the analysis is wrong, not the flight.
"""

import pytest

from debrief.core.igc import load_igc
from debrief.core.metrics import analyse

SIMULATED_GLIDE_RATIO = 33.0 / 1.15  # 28.7


@pytest.fixture(scope="module")
def metrics(synthetic_igc):
    return analyse(load_igc(synthetic_igc))


def test_task_is_completed(metrics):
    assert metrics.completed
    assert not metrics.outlanded
    assert metrics.warnings == ()
    assert len(metrics.legs) == 3
    assert all(leg.completed for leg in metrics.legs)


def test_task_distance_and_speed_are_self_consistent(metrics):
    assert metrics.task_distance_km == pytest.approx(311, abs=5)
    expected = metrics.task_distance_km / (metrics.task_duration_s / 3600.0)
    assert metrics.task_speed_kmh == pytest.approx(expected, rel=1e-9)
    assert 60 < metrics.task_speed_kmh < 110


def test_glide_ratio_recovers_the_simulated_polar(metrics):
    # Phase detection assigns a few thermal entry/exit fixes to cruise, so allow
    # a little slack; anything outside this means the cruise segmentation drifted.
    assert metrics.glide_ratio == pytest.approx(SIMULATED_GLIDE_RATIO, rel=0.08)


def test_climb_rate_recovers_the_simulated_climb(metrics):
    # The generator decays climb ~8% per leg from 2.2 m/s, averaging near 2.0.
    assert metrics.average_climb_ms == pytest.approx(2.0, abs=0.35)
    assert metrics.thermal_count >= 8
    for thermal in metrics.thermals:
        assert thermal.height_gain > 0
        assert thermal.average_climb_ms > 0


def test_climb_decays_over_the_flight(metrics):
    """Later legs were flown in weaker lift; the metrics should show it."""
    first, last = metrics.legs[0], metrics.legs[-1]
    assert first.average_climb_ms > last.average_climb_ms


def test_circling_share_is_plausible(metrics):
    assert 15 < metrics.percent_circling < 60
    for leg in metrics.legs:
        assert 0 < leg.percent_circling < 70


def test_detour_is_small_because_the_glider_cruises_straight(metrics):
    """Guards the detour definition: counting circling as detour gives ~+50%."""
    assert metrics.detour_percent == pytest.approx(0.0, abs=6.0)
    for leg in metrics.legs:
        assert leg.detour_percent < 10.0


def test_track_distance_splits_into_cruise_and_circling(metrics):
    for leg in metrics.legs:
        assert leg.circling_distance_km > 0
        assert leg.cruise_distance_km + leg.circling_distance_km == pytest.approx(
            leg.distance_flown_km, rel=1e-6
        )


def test_energy_band_is_reported_per_leg(metrics):
    for leg in metrics.legs:
        assert leg.altitude_min < leg.altitude_max
        assert leg.altitude_min <= leg.altitude_mean <= leg.altitude_max
        assert leg.working_band > 300
        assert leg.height_gain > 0 and leg.height_loss > 0


def test_final_glide_is_measured(metrics):
    assert metrics.final_glide_km is not None
    assert metrics.final_glide_height is not None
    assert metrics.final_glide_km > 0
    # A final glide must lose height, not gain it.
    assert metrics.final_glide_height > 0


def test_leg_durations_sum_to_task_duration(metrics):
    total = sum(leg.duration_s for leg in metrics.legs)
    assert total == pytest.approx(metrics.task_duration_s)
    assert metrics.start_time < metrics.finish_time


def test_slower_pilot_scores_a_lower_task_speed(synthetic_igc, second_pilot_igc):
    """The comparison axis in miniature: two pilots, one task, different numbers."""
    fast = analyse(load_igc(synthetic_igc))
    slow = analyse(load_igc(second_pilot_igc))
    assert fast.task_key == slow.task_key
    assert fast.task_speed_kmh > slow.task_speed_kmh
    assert fast.average_climb_ms > slow.average_climb_ms
    assert fast.task_distance_km == pytest.approx(slow.task_distance_km, rel=0.01)


def test_final_glide_is_found_when_the_last_leg_has_no_climb(short_final_leg_igc):
    """Regression: the final glide must be measured from the last climb anywhere
    on the task, not only from a climb on the final leg.

    Found on a real 401 km flight out of Rieti, where the last thermal was on
    leg 6 of 7 and the 8 km run-in was pure glide. The old code looked only at
    legs[-1].thermals, so it reported no final glide at all — on precisely the
    flights where the final glide is the interesting part.
    """
    metrics = analyse(load_igc(short_final_leg_igc))

    assert metrics.legs[-1].thermal_count == 0, "fixture no longer exercises the bug"
    assert any(leg.thermal_count for leg in metrics.legs[:-1])

    assert metrics.final_glide_km is not None
    assert metrics.final_glide_height is not None
    assert metrics.final_glide_km > metrics.legs[-1].task_distance_km, (
        "the glide must extend back past the final turnpoint into the previous leg"
    )
    # Height must be lost, and the implied glide ratio has to be physical.
    assert metrics.final_glide_height > 0
    implied = metrics.final_glide_km * 1000.0 / metrics.final_glide_height
    assert 15 < implied < 60


def test_flight_without_a_task_cannot_be_analysed(tmp_path, synthetic_igc):
    stripped = tmp_path / "notask.igc"
    stripped.write_text(
        "\n".join(
            line
            for line in synthetic_igc.read_text().splitlines()
            if not line.startswith(("LCU::C", "LSEEYOU"))
        )
        + "\n"
    )
    flight = load_igc(stripped)
    assert flight.task is None
    with pytest.raises(ValueError, match="no declared task"):
        analyse(flight)
