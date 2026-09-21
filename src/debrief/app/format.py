"""Turning numbers into the strings the views show.

Three shapes cover everything on screen, and the reason they are here rather
than defined next to each view is that they had drifted: two modules carried a
``_duration`` that formatted differently and disagreed about what to do with
zero, so the same quantity read one way on one tab and another way on the next.

The rule they share is that **missing and zero are different**. A metric nobody
could compute shows an em dash; a metric that genuinely came out zero shows
zero. Collapsing the two is how "no circling at all" came to look like "we
could not tell", which is the more alarming of the two readings and the wrong
one.
"""

from __future__ import annotations

MISSING = "—"  # em dash


def number(value: float | None, digits: int = 1, suffix: str = "", sign: bool = False) -> str:
    """A measurement, to a fixed number of decimals."""
    if value is None:
        return MISSING
    return f"{value:{'+' if sign else ''}.{digits}f}{suffix}"


def gap(seconds: float | None) -> str:
    """A difference between two times, as ``m:ss`` and signed.

    Minutes rather than hours because a gap is small: the leg deltas and the
    stretch costs are the things wearing this, and none of them is an hour.
    """
    if seconds is None:
        return MISSING
    total = int(abs(seconds))
    # The sign follows what is printed, not what was passed: half a second
    # behind rounds to no gap at all, and "-0:00" reads as a negative nothing.
    sign = "-" if seconds < 0 and total else ""
    return f"{sign}{total // 60}:{total % 60:02d}"


def clock(seconds: float | None) -> str:
    """A span of flying, as ``h:mm:ss``.

    Past an hour ``m:ss`` stops reading as a duration — an hour and a half into
    a task shows as "90:00" and is read as ninety hours.
    """
    if seconds is None:
        return MISSING
    total = int(seconds)
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"
