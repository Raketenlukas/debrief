"""Task progress and the gap to a reference.

The invariant that matters most is the telescoping one: the seconds a chart
attributes to each stretch of course must add up to the gap at the end. If they
do not, the map is pointing at the wrong kilometre — which is worse than not
drawing it, because it reads as an answer.
"""

import datetime as dt

import pytest

from debrief.core.igc import load_igc
from debrief.core.metrics import analyse_or_summarise
from debrief.core.progress import (
    FIELD_BEST,
    FIELD_MEDIAN,
    ProgressComparison,
    TaskRuler,
    delta_samples,
    delta_segments,
    field_reference,
    progress_track,
)
from tests.fixtures.synthetic import DEFAULT_TASK, write_igc


@pytest.fixture(scope="module")
def fast(synthetic_igc):
    return analyse_or_summarise(load_igc(synthetic_igc))


@pytest.fixture(scope="module")
def slow(second_pilot_igc):
    return analyse_or_summarise(load_igc(second_pilot_igc))


@pytest.fixture(scope="module")
def late_starter(tmp_path_factory):
    """The same flying, started forty minutes later.

    This is the case that makes clock alignment and start alignment disagree,
    and it is the ordinary case in a competition: the gate is open for hours.
    """
    path = tmp_path_factory.mktemp("igc") / "LT.igc"
    write_igc(
        path,
        task=DEFAULT_TASK,
        pilot="Carla Spaet",
        competition_id="LT",
        glider_id="D-5555",
        start_time=dt.time(12, 10),
    )
    return analyse_or_summarise(load_igc(path))


# --- the ruler ---------------------------------------------------------------


def test_the_ruler_measures_the_declared_course(fast):
    ruler = TaskRuler.from_task(fast.task)
    assert len(ruler.leg_length_m) == fast.task.n_legs
    assert len(ruler.cumulative_m) == fast.task.n_legs + 1
    assert ruler.cumulative_m[0] == 0.0
    assert ruler.total_m == pytest.approx(sum(ruler.leg_length_m))
    # The same course the metrics scored, to within the difference between
    # opensoar's rounding points and the turnpoint centres.
    assert ruler.total_m / 1000.0 == pytest.approx(fast.task_distance_km, rel=0.02)


def test_the_ruler_names_where_a_distance_is(fast):
    ruler = TaskRuler.from_task(fast.task)
    assert ruler.leg_at(0.0) == 0
    assert ruler.leg_at(ruler.cumulative_m[1] + 1.0) == 1
    assert ruler.leg_at(ruler.total_m * 2) == fast.task.n_legs - 1

    middle = (ruler.cumulative_m[1] + ruler.cumulative_m[2]) / 2.0
    described = ruler.describe(middle)
    assert "leg 2" in described
    assert fast.task.points[2].name in described
    assert "km before" in described


# --- the track ---------------------------------------------------------------


def test_task_distance_never_goes_backwards(fast):
    """Circling, a wide turnpoint rounding and a detour all move the glider
    without moving it along the course. Progress has to hold, not dip, or
    'when did you first reach here' stops having one answer."""
    track = progress_track(fast)
    assert track is not None
    assert all(b >= a for a, b in zip(track.distance_m, track.distance_m[1:], strict=False))


def test_the_track_spans_the_whole_task(fast):
    track = progress_track(fast)
    ruler = TaskRuler.from_task(fast.task)
    assert track.distance_m[0] < ruler.total_m * 0.02
    assert track.max_distance_m == pytest.approx(ruler.total_m, rel=0.03)


def test_every_array_stays_the_same_length(fast):
    track = progress_track(fast)
    lengths = {
        len(track.distance_m),
        len(track.elapsed_s),
        len(track.clock_s),
        len(track.altitude_m),
        len(track.latitude),
        len(track.longitude),
        len(track.times),
    }
    assert lengths == {len(track)}


def test_sampling_thins_the_trace(fast):
    """A 1-second logger is far finer than a gap between two gliders needs."""
    track = progress_track(fast, sample_seconds=30.0)
    assert len(track) < len(fast.flight.trace)
    spans = [b - a for a, b in zip(track.elapsed_s, track.elapsed_s[1:], strict=False)]
    # Leg boundaries are always kept, so a handful of short steps are expected;
    # the body of the track must respect the requested spacing.
    assert sorted(spans)[len(spans) // 2] >= 25.0


def test_a_flight_without_a_task_has_no_progress(synthetic_igc):
    from debrief.core.metrics import summarise

    free = summarise(load_igc(synthetic_igc))
    assert progress_track(free) is None


def test_lookups_answer_with_the_first_arrival(fast):
    """While circling, one task distance covers many fixes. 'When did you get
    here' means the first of them, not the last."""
    track = progress_track(fast)
    plateau = None
    for index in range(1, len(track) - 1):
        if track.distance_m[index] == track.distance_m[index + 1]:
            plateau = index
            break
    assert plateau is not None, "the synthetic flight should circle somewhere"
    assert track.time_at(track.distance_m[plateau]) == pytest.approx(track.elapsed_s[plateau], abs=1.0)


def test_lookups_outside_the_track_have_no_answer(fast):
    track = progress_track(fast)
    assert track.time_at(track.max_distance_m * 2) is None
    assert track.altitude_at(-1000.0) is None


# --- the gap -----------------------------------------------------------------


def test_a_pilot_against_themselves_is_flat_zero(fast):
    track = progress_track(fast)
    samples = delta_samples(track, track)
    assert all(abs(s.delta_s) < 1e-6 for s in samples)
    assert all(abs(s.delta_altitude_m) < 1e-6 for s in samples)


def test_the_slower_pilot_ends_up_behind(fast, slow):
    reference = progress_track(fast)
    samples = delta_samples(progress_track(slow), reference)
    measured = [s for s in samples if s.delta_s is not None]
    assert measured[0].delta_s == pytest.approx(0.0, abs=30.0)
    assert measured[-1].delta_s > 0


def test_the_final_gap_is_the_difference_in_task_time(fast, slow):
    """The gap measured along the course must agree with the gap the leg
    deltas measure, or two views of the same day would contradict each other."""
    samples = delta_samples(progress_track(slow), progress_track(fast))
    measured = [s for s in samples if s.delta_s is not None]
    expected = slow.task_duration_s - fast.task_duration_s
    assert measured[-1].delta_s == pytest.approx(expected, abs=90.0)


def test_nothing_is_measured_past_the_end_of_the_reference(fast, slow):
    """A reference who landed out cannot say how long the rest should take."""
    reference = progress_track(fast)
    truncated = type(reference)(
        label=reference.label,
        flight_key=reference.flight_key,
        distance_m=reference.distance_m[: len(reference) // 2],
        elapsed_s=reference.elapsed_s[: len(reference) // 2],
        clock_s=reference.clock_s[: len(reference) // 2],
        altitude_m=reference.altitude_m[: len(reference) // 2],
        latitude=reference.latitude[: len(reference) // 2],
        longitude=reference.longitude[: len(reference) // 2],
        times=reference.times[: len(reference) // 2],
    )
    samples = delta_samples(progress_track(slow), truncated)
    assert any(s.delta_s is None for s in samples)
    assert samples[-1].delta_s is None


def test_height_is_compared_at_the_same_point_on_task(fast, slow):
    samples = delta_samples(progress_track(slow), progress_track(fast))
    measured = [s for s in samples if s.delta_altitude_m is not None]
    assert measured
    # Sign convention: positive means this pilot was the higher one.
    reference = progress_track(fast)
    for sample in measured[:50]:
        expected = sample.altitude_m - reference.altitude_at(sample.distance_m)
        assert sample.delta_altitude_m == pytest.approx(expected, abs=1e-6)


def test_clock_and_start_alignment_disagree_by_the_start_gap(fast, late_starter):
    """Started forty minutes apart, the two alignments answer different
    questions and must not be quietly interchangeable."""
    reference = progress_track(fast)
    subject = progress_track(late_starter)

    own = [s.delta_s for s in delta_samples(subject, reference, align="start") if s.delta_s is not None]
    wall = [s.delta_s for s in delta_samples(subject, reference, align="clock") if s.delta_s is not None]
    assert own and wall

    gap = (late_starter.start_time - fast.start_time).total_seconds()
    assert gap == pytest.approx(40 * 60, abs=120)
    assert wall[0] - own[0] == pytest.approx(gap, abs=5.0)


# --- segments ----------------------------------------------------------------


def test_segments_cover_the_track_without_gaps(fast, slow):
    samples = delta_samples(progress_track(slow), progress_track(fast))
    segments = delta_segments(samples, segment_m=2000.0)
    assert segments
    for before, after in zip(segments, segments[1:], strict=False):
        # The shared boundary sample is what keeps the drawn line continuous.
        assert before.end is after.start


def test_segment_losses_add_up_to_the_final_gap(fast, slow):
    """The telescoping invariant. Each segment reports the gap's change across
    it, so the segments must sum to the gap itself — otherwise the map
    attributes the day's minutes to the wrong kilometres."""
    samples = delta_samples(progress_track(slow), progress_track(fast))
    segments = delta_segments(samples)
    losses = [s.lost_s for s in segments if s.lost_s is not None]
    measured = [s.delta_s for s in samples if s.delta_s is not None]
    assert sum(losses) == pytest.approx(measured[-1] - measured[0], abs=1e-6)


def test_a_segment_reports_the_stretch_it_covers(fast, slow):
    samples = delta_samples(progress_track(slow), progress_track(fast))
    for segment in delta_segments(samples, segment_m=2000.0):
        assert segment.seconds > 0
        assert segment.distance_km >= 0
        assert len(segment.path) == len(segment.samples)
        assert segment.path[0] == [segment.start.longitude, segment.start.latitude]


def test_uncomparable_stretches_are_kept_apart_from_measured_ones(fast, slow):
    """A segment half inside and half outside the reference's reach would be
    coloured as though it had been measured."""
    reference = progress_track(fast)
    half = len(reference) // 2
    truncated = type(reference)(
        label=reference.label,
        flight_key=reference.flight_key,
        distance_m=reference.distance_m[:half],
        elapsed_s=reference.elapsed_s[:half],
        clock_s=reference.clock_s[:half],
        altitude_m=reference.altitude_m[:half],
        latitude=reference.latitude[:half],
        longitude=reference.longitude[:half],
        times=reference.times[:half],
    )
    samples = delta_samples(progress_track(slow), truncated)
    for segment in delta_segments(samples):
        measured = {s.delta_s is not None for s in segment.samples}
        assert len(measured) == 1, "a segment must not straddle the reference's end"
    # and the break itself is visible: the two runs do not share a sample
    segments = delta_segments(samples)
    boundaries = [(a, b) for a, b in zip(segments, segments[1:], strict=False) if a.end is not b.start]
    assert len(boundaries) == 1


def test_too_few_samples_make_no_segments():
    assert delta_segments(()) == ()


# --- the field ---------------------------------------------------------------


def test_the_virtual_best_is_never_slower_than_anyone(fast, slow, late_starter):
    tracks = [progress_track(m) for m in (fast, slow, late_starter)]
    best = field_reference(tracks, statistic=FIELD_BEST)
    assert best is not None
    assert best.synthetic

    # The composite is sampled on a 250 m grid and read back by interpolation,
    # so between two grid nodes it can sit a hair above a pilot's own curve
    # where that curve bends. A second of slack covers the gridding; anything
    # larger would mean the composite is not the minimum it claims to be.
    for track in tracks:
        for point in (0.25, 0.5, 0.75):
            distance = track.max_distance_m * point
            assert best.time_at(distance) <= track.time_at(distance) + 1.0


def test_the_median_sits_inside_the_field(fast, slow, late_starter):
    tracks = [progress_track(m) for m in (fast, slow, late_starter)]
    median = field_reference(tracks, statistic=FIELD_MEDIAN)
    distance = min(t.max_distance_m for t in tracks) * 0.5
    times = sorted(t.time_at(distance) for t in tracks)
    assert times[0] <= median.time_at(distance) <= times[-1]


def test_a_field_reference_has_no_track_to_draw(fast, slow):
    best = field_reference([progress_track(fast), progress_track(slow)])
    assert best.latitude == ()
    assert best.longitude == ()


def test_one_flight_is_not_a_field(fast):
    assert field_reference([progress_track(fast)]) is None


# --- the whole comparison ----------------------------------------------------


def test_building_against_a_pilot_marks_that_pilot_as_the_reference(fast, slow):
    progress = ProgressComparison.build([fast, slow], reference=fast)
    assert progress is not None
    assert progress.reference_label == fast.flight.pilot.label
    assert [p.is_reference for p in progress.pilots] == [True, False]
    assert progress.for_key(slow.flight.flight_key).label == slow.flight.pilot.label


def test_building_against_the_field_leaves_every_pilot_measured(fast, slow, late_starter):
    progress = ProgressComparison.build([fast, slow, late_starter], reference=FIELD_BEST)
    assert not any(p.is_reference for p in progress.pilots)
    assert progress.reference.synthetic
    assert all(p.final_delta_s is not None for p in progress.pilots)


def test_the_worst_minutes_come_out_worst_first(fast, slow):
    progress = ProgressComparison.build([fast, slow], reference=fast)
    pilot = progress.for_key(slow.flight.flight_key)
    ranked = pilot.ranked_segments(worst_first=True)
    assert ranked
    assert all(a.lost_s >= b.lost_s for a, b in zip(ranked, ranked[1:], strict=False))
    assert pilot.ranked_segments(worst_first=False)[0] is ranked[-1]


def test_the_lowest_point_against_the_reference_is_found(fast, slow):
    progress = ProgressComparison.build([fast, slow], reference=fast)
    pilot = progress.for_key(slow.flight.flight_key)
    lowest = pilot.lowest_against_reference
    assert lowest is not None
    deltas = [s.delta_altitude_m for s in pilot.samples if s.delta_altitude_m is not None]
    assert lowest.delta_altitude_m == min(deltas)


def test_flights_without_a_task_are_reported_not_dropped_silently(fast, slow):
    from debrief.core.metrics import summarise

    free = summarise(load_igc(slow.flight.source.reference))
    progress = ProgressComparison.build([fast, free], reference=fast)
    assert progress.warnings
    assert any("no task progress" in w for w in progress.warnings)


def test_a_field_reference_needs_more_than_one_flight(fast):
    assert ProgressComparison.build([fast], reference=FIELD_BEST) is None
