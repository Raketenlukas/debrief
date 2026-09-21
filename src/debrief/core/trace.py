"""Slicing a flight trace by time.

A trace is a list of fixes in time order, and almost every question asked of one
is "what happened between these two moments" — a leg, a thermal, a cruise phase,
a stretch of course. Written as a comprehension that filter reads clearly and
costs a full scan, so asking it once per leg and once per thermal turns a
ten-thousand-fix flight into a million comparisons that were never necessary.

Binary search answers the same question in a handful of comparisons, which is
what these helpers are. They matter more than their size suggests: a comparison
between two timezone-aware datetimes is not cheap, and a competition day is
twenty of these flights.

The one precondition is that the trace is ordered by time, which
:func:`debrief.core.igc.load_igc` establishes and keeps.
"""

from __future__ import annotations

import datetime as dt
import itertools
from bisect import bisect_left, bisect_right

from debrief.core.models import Fix


def _when(fix: Fix) -> dt.datetime:
    return fix["datetime"]


def bounds(trace: list[Fix], start: dt.datetime, end: dt.datetime) -> tuple[int, int]:
    """Half-open index range ``[lo, hi)`` covering ``start <= t <= end``.

    Both ends are inclusive in time, which is what callers want: a leg runs from
    its start fix to its end fix and both belong to it.
    """
    return bisect_left(trace, start, key=_when), bisect_right(trace, end, key=_when)


def span(trace: list[Fix], start: dt.datetime, end: dt.datetime) -> list[Fix]:
    """The fixes between two moments, inclusive of both."""
    low, high = bounds(trace, start, end)
    return trace[low:high]


def inside_mask(trace: list[Fix], intervals: list[tuple[dt.datetime, dt.datetime]]) -> bytearray:
    """One truthy byte per fix, set where the fix falls inside any interval.

    Testing each fix against each interval is the obvious way to ask "was the
    glider circling here" and is quadratic; locating each interval instead
    costs one binary search apiece and the answer is then an index lookup.
    """
    inside = bytearray(len(trace))
    for start, end in intervals:
        low, high = bounds(trace, start, end)
        if high > low:
            inside[low:high] = b"\x01" * (high - low)
    return inside


def outside(trace: list[Fix], intervals: list[tuple[dt.datetime, dt.datetime]]) -> list[Fix]:
    """Every fix that falls in none of ``intervals``, in time order.

    Used to take the circling out of a track.
    """
    if not intervals:
        return list(trace)
    inside = inside_mask(trace, intervals)
    return [fix for fix, hidden in zip(trace, inside, strict=True) if not hidden]


def in_order(trace: list[Fix]) -> bool:
    """Whether the trace satisfies the precondition everything else assumes."""
    return all(a["datetime"] <= b["datetime"] for a, b in itertools.pairwise(trace))
