"""Compare several flights over the same task.

A competition debrief is really one question — *where did the day go?* — and it
is only answerable against other people. This module puts a set of analysed
flights side by side and computes the differences that answer it:

leg deltas
    seconds gained or lost on each leg against a chosen reference pilot, and
    the running total. This is the "where did I lose it" view.
start tactics
    when and how high each pilot started, and how long they pushed before the
    first climb.
climb quality
    each pilot's average and best climb against the field's distribution.
cruise efficiency
    achieved glide and inter-thermal speed against the field, and how much
    extra track was flown compared with the straightest pilot.

Comparing flights over *different* tasks is meaningless, so a comparison is
built around one ``task_key`` and says so when flights disagree.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from debrief.core.metrics import FlightMetrics


def _rank(value: float | None, values: list[float], higher_is_better: bool) -> int | None:
    """1-based rank of ``value`` within ``values``."""
    if value is None or not values:
        return None
    ordered = sorted(values, reverse=higher_is_better)
    return ordered.index(value) + 1


def _percentile(value: float | None, values: list[float], higher_is_better: bool) -> float | None:
    """Share of the field this value beats, 0-100."""
    if value is None or len(values) < 2:
        return None
    if higher_is_better:
        beaten = sum(1 for other in values if other < value)
    else:
        beaten = sum(1 for other in values if other > value)
    return 100.0 * beaten / (len(values) - 1)


@dataclass(frozen=True)
class Distribution:
    """The field's spread for one metric, for placing a pilot inside it."""

    values: tuple[float, ...]
    higher_is_better: bool

    @property
    def best(self) -> float | None:
        if not self.values:
            return None
        return max(self.values) if self.higher_is_better else min(self.values)

    @property
    def median(self) -> float | None:
        if not self.values:
            return None
        ordered = sorted(self.values)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[middle]
        return (ordered[middle - 1] + ordered[middle]) / 2.0

    def rank_of(self, value: float | None) -> int | None:
        return _rank(value, list(self.values), self.higher_is_better)

    def percentile_of(self, value: float | None) -> float | None:
        return _percentile(value, list(self.values), self.higher_is_better)


@dataclass(frozen=True)
class LegDelta:
    """One leg, for one pilot, against the reference."""

    index: int
    label: str
    duration_s: float | None
    reference_duration_s: float | None
    speed_kmh: float | None

    @property
    def delta_s(self) -> float | None:
        """Positive means slower than the reference on this leg."""
        if self.duration_s is None or self.reference_duration_s is None:
            return None
        return self.duration_s - self.reference_duration_s


@dataclass(frozen=True)
class FlightComparison:
    """One pilot's place in the day."""

    metrics: FlightMetrics
    legs: tuple[LegDelta, ...]
    total_delta_s: float | None

    @property
    def label(self) -> str:
        return self.metrics.flight.pilot.label

    def cumulative_deltas(self) -> list[float | None]:
        """Running total of leg deltas — where the gap actually opened."""
        running = 0.0
        out: list[float | None] = []
        for leg in self.legs:
            if leg.delta_s is None:
                out.append(None)
                continue
            running += leg.delta_s
            out.append(running)
        return out


@dataclass
class DayComparison:
    """Several flights over one task."""

    flights: tuple[FlightMetrics, ...]
    reference_key: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def build(cls, flights: list[FlightMetrics], reference_key: str | None = None) -> DayComparison:
        """Group flights for comparison, dropping any that flew a different task."""
        warnings: list[str] = []
        with_task = [f for f in flights if f.has_task]
        if len(with_task) != len(flights):
            warnings.append(f"{len(flights) - len(with_task)} flight(s) without a task were left out")
        if not with_task:
            return cls(flights=(), reference_key=None, warnings=tuple(warnings))

        # Comparing across different tasks is meaningless, so keep the task most
        # of the field flew and say what was dropped.
        counts: dict[str, int] = {}
        for flight in with_task:
            counts[flight.task_key] = counts.get(flight.task_key, 0) + 1
        main_task = max(counts, key=lambda key: counts[key])
        same_task = [f for f in with_task if f.task_key == main_task]
        if len(same_task) != len(with_task):
            warnings.append(
                f"{len(with_task) - len(same_task)} flight(s) flew a different task and were left out"
            )

        ordered = sorted(
            same_task,
            key=lambda f: (f.task_speed_kmh is None, -(f.task_speed_kmh or 0.0)),
        )
        if reference_key is None and ordered:
            reference_key = ordered[0].flight.flight_key  # fastest by default
        return cls(flights=tuple(ordered), reference_key=reference_key, warnings=tuple(warnings))

    # -- the field -------------------------------------------------------

    def _values(self, attribute: str) -> list[float]:
        out = []
        for flight in self.flights:
            value = getattr(flight, attribute, None)
            if value is not None:
                out.append(float(value))
        return out

    def distribution(self, attribute: str, higher_is_better: bool) -> Distribution:
        return Distribution(tuple(self._values(attribute)), higher_is_better)

    @property
    def reference(self) -> FlightMetrics | None:
        for flight in self.flights:
            if flight.flight.flight_key == self.reference_key:
                return flight
        return self.flights[0] if self.flights else None

    @property
    def task_label(self) -> str | None:
        return self.flights[0].task.label if self.flights else None

    # -- per pilot -------------------------------------------------------

    def comparison_for(self, metrics: FlightMetrics) -> FlightComparison:
        reference = self.reference
        reference_legs = list(reference.legs) if reference else []

        legs = []
        for index, leg in enumerate(metrics.legs):
            reference_leg = reference_legs[index] if index < len(reference_legs) else None
            legs.append(
                LegDelta(
                    index=index,
                    label=leg.label,
                    duration_s=leg.duration_s,
                    reference_duration_s=reference_leg.duration_s if reference_leg else None,
                    speed_kmh=leg.speed_kmh,
                )
            )

        deltas = [leg.delta_s for leg in legs if leg.delta_s is not None]
        return FlightComparison(
            metrics=metrics,
            legs=tuple(legs),
            total_delta_s=sum(deltas) if deltas else None,
        )

    def comparisons(self) -> list[FlightComparison]:
        return [self.comparison_for(flight) for flight in self.flights]

    # -- time alignment --------------------------------------------------

    def elapsed_seconds(self, metrics: FlightMetrics, when: dt.datetime) -> float | None:
        """Seconds since that pilot's own start.

        Aligning on each pilot's start rather than the clock is what makes two
        flights comparable when they started twenty minutes apart: it answers
        "how was I doing at this point of *my* task", not "what was everyone
        doing at 14:30".
        """
        if metrics.start_time is None:
            return None
        return (when - metrics.start_time).total_seconds()
