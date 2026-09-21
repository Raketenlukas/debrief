"""Number and time formatting.

These replaced three near-duplicates that disagreed about zero, so the
distinction between "could not be measured" and "measured, and it was zero" is
the thing worth pinning down: collapsing them made a flight with no circling
read as a flight whose circling was unknown.
"""

import pytest

from debrief.app.format import MISSING, clock, gap, number


@pytest.mark.parametrize("formatter", [number, gap, clock])
def test_nothing_measurable_shows_an_em_dash(formatter):
    assert formatter(None) == MISSING


@pytest.mark.parametrize("formatter", [number, gap, clock])
def test_a_genuine_zero_is_not_an_em_dash(formatter):
    assert formatter(0) != MISSING
    assert formatter(0.0) != MISSING


def test_numbers_keep_the_decimals_they_were_asked_for():
    assert number(12.345) == "12.3"
    assert number(12.345, 2) == "12.35"
    assert number(12.345, 0) == "12"
    assert number(12.3, 1, " km/h") == "12.3 km/h"


def test_a_number_can_carry_its_sign():
    assert number(3.5, 1, sign=True) == "+3.5"
    assert number(-3.5, 1, sign=True) == "-3.5"
    assert number(0.0, 1, sign=True) == "+0.0"
    assert number(3.5) == "3.5"


def test_a_gap_is_minutes_and_seconds_and_signed():
    assert gap(0) == "0:00"
    assert gap(59) == "0:59"
    assert gap(60) == "1:00"
    assert gap(605) == "10:05"
    assert gap(-605) == "-10:05"


def test_a_gap_rounds_toward_zero_rather_than_flipping_sign():
    assert gap(-0.4) == "0:00"
    assert gap(0.9) == "0:00"


def test_a_span_of_flying_reads_as_a_clock():
    assert clock(0) == "0:00:00"
    assert clock(59) == "0:00:59"
    assert clock(3600) == "1:00:00"
    assert clock(6558) == "1:49:18"


def test_an_hour_and_a_half_does_not_read_as_ninety_hours():
    """The reason clock exists next to gap: m:ss past an hour is ambiguous."""
    assert clock(5400) == "1:30:00"
    assert gap(5400) == "90:00"
