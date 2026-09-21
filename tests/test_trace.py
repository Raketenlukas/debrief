"""Slicing a trace by time.

These replaced comprehensions that scanned the whole trace, so the thing to
pin down is that they answer *exactly* the same question — inclusive at both
ends, empty where the old filter was empty — and not merely a similar one.
"""

import datetime as dt

import pytest

from debrief.core.trace import bounds, in_order, inside_mask, outside, span

UTC = dt.UTC


def at(second: int) -> dt.datetime:
    """Seconds past noon; negatives and values past a minute are fine."""
    return dt.datetime(2024, 6, 15, 12, 0, tzinfo=UTC) + dt.timedelta(seconds=second)


@pytest.fixture
def trace():
    return [{"datetime": at(s), "lat": 50.0 + s, "lon": 8.0} for s in range(0, 60, 2)]


def scan(trace, start, end):
    """The comprehension these helpers replaced, kept as the oracle."""
    return [f for f in trace if start <= f["datetime"] <= end]


def test_both_ends_are_inclusive(trace):
    picked = span(trace, at(10), at(20))
    assert [f["datetime"] for f in picked] == [at(10), at(12), at(14), at(16), at(18), at(20)]


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (at(0), at(58)),  # everything
        (at(-10), at(200)),  # wider than the trace
        (at(11), at(19)),  # between fixes at both ends
        (at(10), at(10)),  # a single fix
        (at(11), at(11)),  # a moment with no fix
        (at(40), at(10)),  # inverted
        (at(100), at(200)),  # entirely after
        (at(-200), at(-100)),  # entirely before
    ],
)
def test_it_answers_what_the_scan_answered(trace, start, end):
    assert span(trace, start, end) == scan(trace, start, end)


def test_bounds_are_a_half_open_index_range(trace):
    low, high = bounds(trace, at(10), at(20))
    assert trace[low:high] == span(trace, at(10), at(20))
    assert trace[low]["datetime"] == at(10)
    assert trace[high - 1]["datetime"] == at(20)


def test_an_empty_trace_slices_to_nothing():
    assert span([], at(0), at(10)) == []
    assert bounds([], at(0), at(10)) == (0, 0)
    assert outside([], [(at(0), at(10))]) == []


def test_the_mask_marks_the_fixes_inside_any_interval(trace):
    mask = inside_mask(trace, [(at(10), at(16)), (at(30), at(34))])
    marked = [f["datetime"] for f, hit in zip(trace, mask, strict=True) if hit]
    assert marked == [at(10), at(12), at(14), at(16), at(30), at(32), at(34)]


def test_overlapping_intervals_do_not_double_count(trace):
    once = inside_mask(trace, [(at(10), at(20))])
    twice = inside_mask(trace, [(at(10), at(16)), (at(14), at(20))])
    assert once == twice


def test_outside_is_the_complement_of_the_mask(trace):
    intervals = [(at(10), at(16)), (at(30), at(34))]
    kept = outside(trace, intervals)
    mask = inside_mask(trace, intervals)
    assert kept == [f for f, hit in zip(trace, mask, strict=True) if not hit]
    assert len(kept) + sum(mask) == len(trace)


def test_no_intervals_keeps_everything_and_copies_it(trace):
    kept = outside(trace, [])
    assert kept == trace
    assert kept is not trace


def test_order_is_preserved(trace):
    kept = outside(trace, [(at(10), at(16))])
    assert [f["datetime"] for f in kept] == sorted(f["datetime"] for f in kept)


def test_in_order_recognises_the_precondition(trace):
    assert in_order(trace)
    assert in_order([])
    assert in_order(trace[:1])
    shuffled = [trace[3], trace[0], trace[5]]
    assert not in_order(shuffled)


def test_repeated_timestamps_are_still_in_order():
    """A logger writing two fixes for one second is odd but not out of order,
    and sorting such a trace would be a change for no reason."""
    same = [{"datetime": at(5)}, {"datetime": at(5)}, {"datetime": at(6)}]
    assert in_order(same)
    assert span(same, at(5), at(5)) == same[:2]
