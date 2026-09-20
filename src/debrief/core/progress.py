"""Where the time went: task progress, and each pilot's gap to a reference.

A leg delta says *which leg* cost you three minutes. It cannot say whether you
lost them in one bad thermal, in a slow ten kilometres of cruise, or by rounding
a turnpoint wide. That needs a finer ruler, and the ruler is **task distance**.

The idea is one substitution. Instead of comparing two pilots at the same
*moment* — which is meaningless when they started twenty minutes apart and are
forty kilometres from each other — compare them at the same *point on the task*:

    delta(d) = (time this pilot took to reach task distance d)
             - (time the reference took to reach task distance d)

``delta`` is flat where two pilots progressed equally and rises where one fell
behind, so its *slope* is the answer to "where did I lose it". Over any stretch
the rise is exactly the seconds lost on that stretch, which is what the map
colours and what the tables count. The same substitution gives the height
question — at the same point on task, how much higher or lower was I — which is
usually the cause of which the time is the symptom.

Task distance is measured the way a scorer measures it: how far along the
declared course the glider has got, not how far it has flown. Flying away from
the next turnpoint earns nothing, and a detour costs distance rather than
silently adding it. It is also forced to never decrease, so "when did you first
reach d" is a well-defined question even while circling.

Nothing here knows about maps or charts. It answers in seconds, metres and
positions; the app decides how to draw them.
"""

from __future__ import annotations

import datetime as dt
from bisect import bisect_left
from dataclasses import dataclass

from pyproj import Geod

from debrief.core.metrics import DEFAULT_GPS_ALTITUDE, FlightMetrics, fix_altitude
from debrief.core.models import TaskDef

GEOD = Geod(ellps="WGS84")

# How often the task position is sampled. IGC files log every 1-4 s, which is far
# finer than this analysis needs: the gap between two pilots does not carry
# meaning at one-second resolution, and thinning keeps a twenty-pilot day cheap.
SAMPLE_SECONDS = 5.0

# How much *course* each coloured piece of track covers.
#
# Course rather than time, and this is not a detail. A stretch's cost is the
# time it took minus the time the reference took over the same stretch — so a
# stretch has to *be* a stretch of course. Cut the track into equal minutes
# instead and a five-minute climb becomes five pieces that advance nobody
# anywhere: each one honestly reports no loss, and the whole cost of the climb
# lands on the single piece where the glider finally moved on. Equal kilometres
# put the cost where the flying was: the piece spanning a climb is drawn
# through the circling itself, because that is the track the glider flew while
# covering those kilometres.
#
# Ten kilometres, because that is roughly one climb and the glide it buys. Two
# pilots almost never climb in the same place, so at much finer resolution the
# measurement is dominated by *where* each of them stopped: a stretch is red
# because you climbed in it and the next is blue because the reference did,
# and the pair of them says nothing about who was quicker. A stretch holding a
# climb and its glide compares the thing a pilot actually chooses. Finer is
# still worth offering — it is how you find the one thermal that cost the day —
# which is why this is a default rather than a constant.
SEGMENT_M = 10000.0

# The stretch lengths offered to a reader, shortest first.
SEGMENT_CHOICES_M = (2000.0, 10000.0, 25000.0)

# Resolution of the synthetic field reference. 250 m is well below the distance
# over which a gap changes meaningfully.
FIELD_GRID_M = 250.0

# How a pilot is compared against the whole field rather than one rival.
FIELD_BEST = "field_best"
FIELD_MEDIAN = "field_median"

ALIGN_START = "start"
ALIGN_CLOCK = "clock"


def _interpolate(xs: tuple[float, ...], ys: tuple[float, ...], x: float) -> float | None:
    """``y`` at ``x``, linearly, where ``xs`` is sorted and may have flat runs.

    A flat run in ``xs`` is a pilot circling: many samples at one task distance.
    ``bisect_left`` lands on the *first* of them, so the answer is the value on
    arrival rather than on departure — "when did you first get here", which is
    the question the gap is asking.
    """
    if not xs or x < xs[0] or x > xs[-1]:
        return None
    index = bisect_left(xs, x)
    if index == 0:
        return ys[0]
    lower, upper = xs[index - 1], xs[index]
    if upper <= lower:  # pragma: no cover - only reachable on a degenerate grid
        return ys[index]
    fraction = (x - lower) / (upper - lower)
    return ys[index - 1] + fraction * (ys[index] - ys[index - 1])


@dataclass(frozen=True)
class TaskRuler:
    """The declared course, read as a distance scale.

    Turns a task distance back into words — which leg, and how far before the
    next turnpoint — so a finding can be named as well as drawn.
    """

    task: TaskDef
    leg_length_m: tuple[float, ...]
    cumulative_m: tuple[float, ...]  # one entry per turnpoint, so n_legs + 1

    @classmethod
    def from_task(cls, task: TaskDef) -> TaskRuler:
        lengths = []
        for index in range(task.n_legs):
            here, there = task.points[index], task.points[index + 1]
            lengths.append(GEOD.inv(here.longitude, here.latitude, there.longitude, there.latitude)[2])
        cumulative = [0.0]
        for length in lengths:
            cumulative.append(cumulative[-1] + length)
        return cls(task=task, leg_length_m=tuple(lengths), cumulative_m=tuple(cumulative))

    @property
    def total_m(self) -> float:
        return self.cumulative_m[-1]

    def leg_at(self, distance_m: float) -> int:
        """Index of the leg this distance falls on, clamped to the task."""
        if not self.leg_length_m:
            return 0
        for index in range(len(self.leg_length_m)):
            if distance_m < self.cumulative_m[index + 1]:
                return index
        return len(self.leg_length_m) - 1

    def describe(self, distance_m: float) -> str:
        """Where this is, in the language of the task briefing."""
        if not self.leg_length_m:
            return "—"
        leg = self.leg_at(distance_m)
        to_go = self.cumulative_m[leg + 1] - distance_m
        next_point = self.task.points[leg + 1].name
        if to_go <= 0:
            return f"leg {leg + 1}, past {next_point}"
        return f"leg {leg + 1}, {to_go / 1000.0:.0f} km before {next_point}"


@dataclass(frozen=True)
class ProgressTrack:
    """One pilot's progress along the task, as a lookup by task distance.

    Every array is parallel and ordered both by time and by distance: task
    distance is forced to never decrease, so the two orders are the same one.
    That is what makes the arrays bisectable.
    """

    label: str
    flight_key: str | None
    distance_m: tuple[float, ...]
    elapsed_s: tuple[float, ...]  # since this pilot's own start
    clock_s: tuple[float, ...]  # POSIX seconds, for absolute-time alignment
    altitude_m: tuple[float, ...]
    latitude: tuple[float, ...]
    longitude: tuple[float, ...]
    times: tuple[dt.datetime, ...]
    # A field reference is stitched together from many pilots, so it has times
    # and heights but no position and no single pilot behind it.
    synthetic: bool = False

    def __len__(self) -> int:
        return len(self.distance_m)

    @property
    def max_distance_m(self) -> float:
        return self.distance_m[-1] if self.distance_m else 0.0

    def time_at(self, distance_m: float, clock: bool = False) -> float | None:
        """Seconds at which this pilot first reached ``distance_m``."""
        return _interpolate(self.distance_m, self.clock_s if clock else self.elapsed_s, distance_m)

    def altitude_at(self, distance_m: float) -> float | None:
        """Height on first reaching ``distance_m``."""
        return _interpolate(self.distance_m, self.altitude_m, distance_m)


def progress_track(
    metrics: FlightMetrics,
    sample_seconds: float = SAMPLE_SECONDS,
    gps: bool = DEFAULT_GPS_ALTITUDE,
) -> ProgressTrack | None:
    """Sample one analysed flight into task distance over time.

    Returns ``None`` for a flight with no task or no started leg: there is no
    course to measure progress along.
    """
    if metrics.task is None or not metrics.legs or metrics.start_time is None:
        return None

    ruler = TaskRuler.from_task(metrics.task)
    if not ruler.leg_length_m:
        return None

    trace = metrics.flight.trace
    start = metrics.start_time

    distance: list[float] = []
    elapsed: list[float] = []
    clock: list[float] = []
    altitude: list[float] = []
    lats: list[float] = []
    lons: list[float] = []
    times: list[dt.datetime] = []

    achieved_max = 0.0
    last_sample: dt.datetime | None = None

    for leg in metrics.legs:
        if leg.index + 1 >= len(metrics.task.points):  # pragma: no cover - defensive
            continue
        target = metrics.task.points[leg.index + 1]
        leg_start = ruler.cumulative_m[leg.index]
        leg_length = ruler.leg_length_m[leg.index]
        span = [f for f in trace if leg.start_time <= f["datetime"] <= leg.end_time]

        for position, fix in enumerate(span):
            # Consecutive legs share their boundary fix — the rounding fix ends
            # one leg and starts the next — and a duplicated sample is a
            # zero-length step that makes rates divide by zero.
            if last_sample is not None and fix["datetime"] <= last_sample:
                continue
            is_edge = position == 0 or position == len(span) - 1
            if not is_edge and last_sample is not None:
                if (fix["datetime"] - last_sample).total_seconds() < sample_seconds:
                    continue

            remaining = GEOD.inv(fix["lon"], fix["lat"], target.longitude, target.latitude)[2]
            # Distance *achieved*, as a scorer counts it: everything behind the
            # glider on the course line. Flying away from the turnpoint reduces
            # it, which the running maximum below then holds flat.
            achieved = leg_start + (leg_length - remaining)
            achieved_max = max(achieved_max, achieved)

            distance.append(achieved_max)
            elapsed.append((fix["datetime"] - start).total_seconds())
            clock.append(fix["datetime"].timestamp())
            altitude.append(fix_altitude(fix, gps))
            lats.append(fix["lat"])
            lons.append(fix["lon"])
            times.append(fix["datetime"])
            last_sample = fix["datetime"]

    if len(distance) < 2:
        return None

    return ProgressTrack(
        label=metrics.flight.pilot.label,
        flight_key=metrics.flight.flight_key,
        distance_m=tuple(distance),
        elapsed_s=tuple(elapsed),
        clock_s=tuple(clock),
        altitude_m=tuple(altitude),
        latitude=tuple(lats),
        longitude=tuple(lons),
        times=tuple(times),
    )


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def field_reference(
    tracks: list[ProgressTrack],
    statistic: str = FIELD_BEST,
    grid_m: float = FIELD_GRID_M,
) -> ProgressTrack | None:
    """A reference stitched together from the whole field.

    ``field_best`` is the *virtual* best: at each point on the course, the
    quickest anyone got there. No pilot flew it, which is the point — it is the
    day's ceiling, and the gap to it is what was theoretically available.
    ``field_median`` is the middle of the field at each point, which answers
    "was that stretch bad for me or bad for everybody".

    Both are composites, so their height is the height of whoever supplied the
    time (best) or the field's middle height (median), not one pilot's barogram.
    """
    usable = [t for t in tracks if len(t) >= 2]
    if len(usable) < 2:
        return None

    reach = max(t.max_distance_m for t in usable)
    steps = max(int(reach // grid_m), 1)

    distance: list[float] = []
    elapsed: list[float] = []
    clock: list[float] = []
    altitude: list[float] = []

    for step in range(steps + 1):
        point = min(step * grid_m, reach)
        candidates = [
            (t.time_at(point), t.time_at(point, clock=True), t.altitude_at(point))
            for t in usable
            if point <= t.max_distance_m
        ]
        candidates = [c for c in candidates if c[0] is not None and c[1] is not None]
        if not candidates:
            continue

        if statistic == FIELD_BEST:
            own, absolute, height = min(candidates, key=lambda c: c[0])
        else:
            own = _median([c[0] for c in candidates])
            absolute = _median([c[1] for c in candidates])
            heights = [c[2] for c in candidates if c[2] is not None]
            height = _median(heights) if heights else None

        distance.append(point)
        elapsed.append(own)
        clock.append(absolute)
        altitude.append(height if height is not None else 0.0)

    if len(distance) < 2:
        return None

    label = "the field's best" if statistic == FIELD_BEST else "the field's median"
    return ProgressTrack(
        label=label,
        flight_key=None,
        distance_m=tuple(distance),
        elapsed_s=tuple(elapsed),
        clock_s=tuple(clock),
        altitude_m=tuple(altitude),
        latitude=(),
        longitude=(),
        times=(),
        synthetic=True,
    )


@dataclass(frozen=True)
class DeltaSample:
    """One sample of a pilot's track, measured against the reference.

    Both differences are read at this sample's point on the course, on first
    arrival for either pilot — one rule, no exceptions, which is what makes a
    pilot measured against themselves come out as exactly zero.

    ``delta_s`` is positive when this pilot took longer to get here: behind.
    ``delta_altitude_m`` is positive when this pilot passed this point higher
    than the reference did.

    Both are ``None`` past the end of the reference's own track: there is
    nothing to compare against once the reference has stopped.
    """

    time: dt.datetime
    elapsed_s: float
    distance_m: float
    latitude: float
    longitude: float
    altitude_m: float
    delta_s: float | None
    delta_altitude_m: float | None


def delta_samples(
    track: ProgressTrack, reference: ProgressTrack, align: str = ALIGN_START
) -> tuple[DeltaSample, ...]:
    """Measure one track against a reference, point by point along the task.

    ``align="start"`` measures from each pilot's own start, which is what a
    racing task scores. ``align="clock"`` measures on the wall clock, which
    answers a different question — who was where when the day was working.
    """
    clock = align == ALIGN_CLOCK

    out = []
    for index in range(len(track)):
        point = track.distance_m[index]
        # Both sides are read the same way — first arrival at this point on the
        # course. Mixing the two, taking this pilot's clock *now* against the
        # reference's arrival, looks equivalent and is not: while a pilot
        # circles their clock runs and their course position does not, so the
        # gap climbs through every thermal and then drops back when the
        # reference's own climb at the same place finally enters the lookup.
        # Two identical flights would draw as alternating red and blue.
        own_time = track.time_at(point, clock=clock)
        own_height = track.altitude_at(point)
        reference_time = reference.time_at(point, clock=clock)
        reference_height = reference.altitude_at(point)
        out.append(
            DeltaSample(
                time=track.times[index],
                elapsed_s=track.elapsed_s[index],
                distance_m=point,
                latitude=track.latitude[index],
                longitude=track.longitude[index],
                altitude_m=track.altitude_m[index],
                delta_s=(None if reference_time is None or own_time is None else own_time - reference_time),
                delta_altitude_m=(
                    None if reference_height is None or own_height is None else own_height - reference_height
                ),
            )
        )
    return tuple(out)


@dataclass(frozen=True)
class DeltaSegment:
    """A short stretch of track, with what it cost against the reference."""

    samples: tuple[DeltaSample, ...]

    @property
    def start(self) -> DeltaSample:
        return self.samples[0]

    @property
    def end(self) -> DeltaSample:
        return self.samples[-1]

    @property
    def seconds(self) -> float:
        return self.end.elapsed_s - self.start.elapsed_s

    @property
    def lost_s(self) -> float | None:
        """Seconds lost to the reference over this stretch; negative is gained.

        The gap at the end minus the gap at the start, which is algebraically
        the time this pilot took over the stretch minus the time the reference
        took over the same stretch of course.
        """
        if self.start.delta_s is None or self.end.delta_s is None:
            return None
        return self.end.delta_s - self.start.delta_s

    @property
    def distance_km(self) -> float:
        return (self.end.distance_m - self.start.distance_m) / 1000.0

    @property
    def height_delta_m(self) -> float | None:
        """Height against the reference where this stretch ends."""
        return self.end.delta_altitude_m

    @property
    def height_change_m(self) -> float | None:
        """How much height this pilot gained or lost against the reference here."""
        if self.start.delta_altitude_m is None or self.end.delta_altitude_m is None:
            return None
        return self.end.delta_altitude_m - self.start.delta_altitude_m

    @property
    def path(self) -> list[list[float]]:
        return [[s.longitude, s.latitude] for s in self.samples]


def delta_segments(
    samples: tuple[DeltaSample, ...], segment_m: float = SEGMENT_M
) -> tuple[DeltaSegment, ...]:
    """Chop a measured track into stretches of roughly equal task distance.

    See :data:`SEGMENT_M` for why the stretches are equal in course rather than
    in time. The drawn path is every sample between the two ends, so a stretch
    that took ten minutes because the pilot stopped to climb is drawn through
    that climb, in the colour the climb cost.

    Consecutive segments share their boundary sample, so the drawn line has no
    gaps. A stretch where the reference offers no comparison is kept separate
    rather than merged, so an uncomparable piece is never coloured as if it had
    been measured.
    """
    if len(samples) < 2:
        return ()

    # Split first on whether the reference has anything to say, then cut each
    # run into stretches. Cutting the other way round would let one stretch
    # straddle the point where the reference's track ends: its two ends would
    # be measured against different things, and the seconds it reported would
    # be missing from the total. The two runs do not share a boundary sample,
    # so the drawn line shows a hairline break exactly where the comparison
    # stops — which is honest.
    runs: list[list[DeltaSample]] = [[samples[0]]]
    for sample in samples[1:]:
        if (sample.delta_s is None) != (runs[-1][-1].delta_s is None):
            runs.append([sample])
        else:
            runs[-1].append(sample)

    segments: list[DeltaSegment] = []
    for run in runs:
        current: list[DeltaSample] = [run[0]]
        for sample in run[1:]:
            current.append(sample)
            if sample.distance_m - current[0].distance_m >= segment_m:
                segments.append(DeltaSegment(samples=tuple(current)))
                current = [sample]  # shared, so the line has no gap inside a run
        if len(current) >= 2:
            segments.append(DeltaSegment(samples=tuple(current)))
    return tuple(segments)


@dataclass(frozen=True)
class PilotProgress:
    """One pilot's whole story against the reference."""

    metrics: FlightMetrics
    track: ProgressTrack
    samples: tuple[DeltaSample, ...]
    segments: tuple[DeltaSegment, ...]
    is_reference: bool = False

    @property
    def label(self) -> str:
        return self.metrics.flight.pilot.label

    @property
    def flight_key(self) -> str:
        return self.metrics.flight.flight_key

    @property
    def final_delta_s(self) -> float | None:
        """The gap where the comparison ran out — the whole story in one number."""
        for sample in reversed(self.samples):
            if sample.delta_s is not None:
                return sample.delta_s
        return None

    def ranked_segments(self, worst_first: bool = True) -> list[DeltaSegment]:
        """Segments ordered by what they cost, skipping the uncomparable ones."""
        measured = [s for s in self.segments if s.lost_s is not None]
        return sorted(measured, key=lambda s: s.lost_s, reverse=worst_first)

    @property
    def lowest_against_reference(self) -> DeltaSample | None:
        """The point on task where this pilot was furthest below the reference."""
        measured = [s for s in self.samples if s.delta_altitude_m is not None]
        if not measured:
            return None
        return min(measured, key=lambda s: s.delta_altitude_m)


@dataclass(frozen=True)
class ProgressComparison:
    """Several pilots measured along one task against one reference."""

    reference: ProgressTrack
    ruler: TaskRuler
    align: str
    pilots: tuple[PilotProgress, ...]
    warnings: tuple[str, ...] = ()

    @property
    def reference_label(self) -> str:
        return self.reference.label

    def for_key(self, flight_key: str) -> PilotProgress | None:
        for pilot in self.pilots:
            if pilot.flight_key == flight_key:
                return pilot
        return None

    @classmethod
    def build(
        cls,
        flights: list[FlightMetrics],
        reference: FlightMetrics | str,
        align: str = ALIGN_START,
        sample_seconds: float = SAMPLE_SECONDS,
        segment_m: float = SEGMENT_M,
    ) -> ProgressComparison | None:
        """Measure every flight against ``reference``.

        ``reference`` is either one of the flights or one of the field
        statistics (:data:`FIELD_BEST`, :data:`FIELD_MEDIAN`), which are built
        from the same set of flights.
        """
        warnings: list[str] = []
        tracks: dict[str, ProgressTrack] = {}
        usable: list[FlightMetrics] = []
        for metrics in flights:
            track = progress_track(metrics, sample_seconds=sample_seconds)
            if track is None:
                warnings.append(f"{metrics.flight.pilot.label}: no task progress to measure")
                continue
            tracks[metrics.flight.flight_key] = track
            usable.append(metrics)

        if not usable:
            return None

        if isinstance(reference, str):
            reference_track = field_reference(list(tracks.values()), statistic=reference)
            reference_key: str | None = None
            if reference_track is None:
                warnings.append("not enough flights to build a field reference")
                return None
        else:
            reference_key = reference.flight.flight_key
            reference_track = tracks.get(reference_key)
            if reference_track is None:
                return None

        ruler = TaskRuler.from_task(usable[0].task)

        pilots = []
        for metrics in usable:
            track = tracks[metrics.flight.flight_key]
            samples = delta_samples(track, reference_track, align=align)
            pilots.append(
                PilotProgress(
                    metrics=metrics,
                    track=track,
                    samples=samples,
                    segments=delta_segments(samples, segment_m=segment_m),
                    is_reference=metrics.flight.flight_key == reference_key,
                )
            )

        return cls(
            reference=reference_track,
            ruler=ruler,
            align=align,
            pilots=tuple(pilots),
            warnings=tuple(warnings),
        )
