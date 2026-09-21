"""What the whole field did, rather than what one pilot did.

Three questions, all answered by pooling every flight from one competition day:

where the lift was
    every climb the field found, placed on the ground and carrying its rate.
    One pilot's thermals are a sample of one; forty pilots' thermals are a map
    of the day, and the difference between a line of good climbs and a patch
    everybody struggled in is visible only in the pool.
where the field flew
    track density outside the climbs. Gliders converge on the same ridges,
    streets and valley lines, and the places nobody went are as informative as
    the motorway everyone used.
what the day was worth
    the quickest *energy-consistent* way through the course, assembled from
    pieces people actually flew. The gap to it is what was on offer.

The composite is a ceiling, not a plan. Its stretches were flown by different
pilots in different gliders at different times of day, and you cannot be in two
places at once — so it is read as "this was available", never as "you should
have done this". Which pilot and which glider supplied each stretch is carried
on the stretch for exactly that reason.

The energy constraint is what makes it worth looking at. Simply taking the
quickest crossing of every stretch builds a glider that never climbs: at every
stretch somebody was gliding through, so the minimum always picks a glide, and
the result flies the whole task without stopping and "beats" the winner by
seventy minutes. It is not a ceiling, it is an artefact of taking a minimum
over a population. So the composite carries a height: it may only use a piece
whose pilot entered it no higher than the composite is, and it leaves the
piece having lost exactly the height that pilot lost. Climbs are then not
optional — the composite has to buy its altitude the same way everyone else
did, from the lift that was actually there.

Nothing here knows about maps or charts.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from pyproj import Transformer

from debrief.core.metrics import DEFAULT_GPS_ALTITUDE, FlightMetrics, fix_altitude
from debrief.core.progress import GEOD, ProgressTrack, progress_track
from debrief.core.trace import outside as trace_outside
from debrief.core.trace import span as trace_span

# A climb has to be a climb. The phase detector segments on turn rate, so it
# emits brief wriggles — a course correction, a bump taken in a turn — that are
# not decisions a pilot made and would dominate a map of the day by number.
CLIMB_MIN_GAIN_M = 100.0
CLIMB_MIN_SECONDS = 45.0

# How often a cruising glider is sampled for the density map. Track density is
# a question about routes, not about seconds.
CRUISE_SAMPLE_SECONDS = 15.0

# Grid resolution. Five kilometres is about a thermal's worth of positional
# uncertainty once drift and the choice of where to centre are allowed for:
# finer and the same thermal, found by two pilots twenty minutes apart, lands
# in two different cells.
DEFAULT_CELL_M = 5000.0

CELL_CHOICES_M = (2500.0, 5000.0, 10000.0)

# Slack on the "you cannot borrow a glide that started above you" rule. Heights
# come from GPS and from interpolating onto a stretch boundary, so demanding
# equality to the metre would reject pieces on measurement noise.
HEIGHT_TOLERANCE_M = 30.0

# Most states the search carries forward between stretches. The frontier stays
# far below this on real days; the cap is there so a pathological field cannot
# turn the search super-linear.
MAX_FRONTIER = 2000


@dataclass(frozen=True)
class ClimbPoint:
    """One climb, placed on the ground."""

    pilot: str
    glider: str | None
    latitude: float
    longitude: float
    start_time: dt.datetime
    end_time: dt.datetime
    climb_ms: float
    height_gain: float
    duration_s: float
    entry_altitude: float
    exit_altitude: float
    drift_m: float


@dataclass(frozen=True)
class TrackPoint:
    """One sample of a glider going somewhere, as opposed to going round."""

    pilot: str
    latitude: float
    longitude: float
    altitude: float
    time: dt.datetime


def field_climbs(
    flights: list[FlightMetrics],
    min_gain_m: float = CLIMB_MIN_GAIN_M,
    min_seconds: float = CLIMB_MIN_SECONDS,
) -> tuple[ClimbPoint, ...]:
    """Every worthwhile climb in the field, with where it was and what it gave.

    A climb is placed at the mean of the fixes it spans rather than at its
    entry: a thermal drifts downwind while it is worked, and the entry point is
    where the glider arrived, not where the lift was.
    """
    out: list[ClimbPoint] = []
    for metrics in flights:
        trace = metrics.flight.trace
        pilot = metrics.flight.pilot.label
        glider = metrics.flight.pilot.glider_model
        for thermal in metrics.thermals:
            if thermal.height_gain < min_gain_m or thermal.duration_s < min_seconds:
                continue
            climb_ms = thermal.average_climb_ms
            if climb_ms is None:
                continue
            fixes = trace_span(trace, thermal.start_time, thermal.end_time)
            if len(fixes) < 2:
                continue
            latitude = sum(f["lat"] for f in fixes) / len(fixes)
            longitude = sum(f["lon"] for f in fixes) / len(fixes)
            out.append(
                ClimbPoint(
                    pilot=pilot,
                    glider=glider,
                    latitude=latitude,
                    longitude=longitude,
                    start_time=thermal.start_time,
                    end_time=thermal.end_time,
                    climb_ms=climb_ms,
                    height_gain=thermal.height_gain,
                    duration_s=thermal.duration_s,
                    entry_altitude=thermal.entry_altitude,
                    exit_altitude=thermal.exit_altitude,
                    drift_m=_distance_m(fixes[0]["lat"], fixes[0]["lon"], fixes[-1]["lat"], fixes[-1]["lon"]),
                )
            )
    return tuple(out)


def field_cruise(
    flights: list[FlightMetrics],
    sample_seconds: float = CRUISE_SAMPLE_SECONDS,
    gps: bool = DEFAULT_GPS_ALTITUDE,
) -> tuple[TrackPoint, ...]:
    """Every flight's track with the circling taken out.

    Climbing is where a glider *stops*; leaving it in would pile dozens of
    fixes onto one spot and turn a map of routes into a map of thermals, which
    is the other map.
    """
    out: list[TrackPoint] = []
    for metrics in flights:
        pilot = metrics.flight.pilot.label
        spans = [(t.start_time, t.end_time) for t in metrics.thermals]
        last: dt.datetime | None = None
        for fix in trace_outside(metrics.flight.trace, spans):
            when = fix["datetime"]
            if last is not None and (when - last).total_seconds() < sample_seconds:
                continue
            out.append(
                TrackPoint(
                    pilot=pilot,
                    latitude=fix["lat"],
                    longitude=fix["lon"],
                    altitude=fix_altitude(fix, gps),
                    time=when,
                )
            )
            last = when
    return tuple(out)


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return GEOD.inv(lon1, lat1, lon2, lat2)[2]


@dataclass(frozen=True)
class Cell:
    """One square of the grid, and everything that landed in it."""

    polygon: tuple[tuple[float, float], ...]  # (lon, lat), closed ring
    latitude: float
    longitude: float
    values: tuple[float, ...]
    pilots: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.values)

    @property
    def pilot_count(self) -> int:
        """How many *different* pilots. One pilot circling is not a hot spot."""
        return len(set(self.pilots))

    @property
    def mean(self) -> float:
        return sum(self.values) / len(self.values)

    @property
    def median(self) -> float:
        ordered = sorted(self.values)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[middle]
        return (ordered[middle - 1] + ordered[middle]) / 2.0

    @property
    def best(self) -> float:
        return max(self.values)


def grid(
    points: list[tuple[float, float, float, str]],
    cell_m: float = DEFAULT_CELL_M,
    min_count: int = 1,
) -> tuple[Cell, ...]:
    """Bin ``(latitude, longitude, value, pilot)`` into equal-area squares.

    Binning happens in an azimuthal equidistant projection centred on the
    points themselves, so the cells are genuinely the size they claim to be. A
    degree of longitude is 30% shorter at the Alps than at the Baltic; binning
    in raw degrees would make a "5 km" cell mean something different on every
    competition site, and the comparison between two of them meaningless.
    """
    if not points:
        return ()

    lat0 = sum(p[0] for p in points) / len(points)
    lon0 = sum(p[1] for p in points) / len(points)
    crs = f"+proj=aeqd +lat_0={lat0} +lon_0={lon0} +datum=WGS84 +units=m +no_defs"
    forward = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    back = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)

    buckets: dict[tuple[int, int], list[tuple[float, str]]] = {}
    for latitude, longitude, value, pilot in points:
        x, y = forward.transform(longitude, latitude)
        key = (int(x // cell_m), int(y // cell_m))
        buckets.setdefault(key, []).append((value, pilot))

    cells = []
    for (ix, iy), contents in buckets.items():
        if len(contents) < min_count:
            continue
        x0, y0 = ix * cell_m, iy * cell_m
        x1, y1 = x0 + cell_m, y0 + cell_m
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
        ring = tuple(back.transform(x, y) for x, y in corners)
        centre_lon, centre_lat = back.transform((x0 + x1) / 2, (y0 + y1) / 2)
        cells.append(
            Cell(
                polygon=ring,
                latitude=centre_lat,
                longitude=centre_lon,
                values=tuple(value for value, _ in contents),
                pilots=tuple(pilot for _, pilot in contents),
            )
        )
    return tuple(cells)


def climb_grid(
    climbs: list[ClimbPoint], cell_m: float = DEFAULT_CELL_M, min_count: int = 1
) -> tuple[Cell, ...]:
    """Where the lift was, with each cell carrying the climb rates found in it."""
    return grid(
        [(c.latitude, c.longitude, c.climb_ms, c.pilot) for c in climbs],
        cell_m=cell_m,
        min_count=min_count,
    )


def cruise_grid(
    points: list[TrackPoint], cell_m: float = DEFAULT_CELL_M, min_count: int = 1
) -> tuple[Cell, ...]:
    """Where the field flew, with each cell carrying the heights it was crossed at."""
    return grid(
        [(p.latitude, p.longitude, p.altitude, p.pilot) for p in points],
        cell_m=cell_m,
        min_count=min_count,
    )


@dataclass(frozen=True)
class BestStretch:
    """One stretch of the course, and who covered it quickest."""

    start_m: float
    end_m: float
    seconds: float
    pilot: str
    glider: str | None
    flight_key: str
    path: tuple[tuple[float, float], ...]
    entry_altitude: float | None
    exit_altitude: float | None

    @property
    def distance_m(self) -> float:
        return self.end_m - self.start_m

    @property
    def speed_kmh(self) -> float | None:
        if self.seconds <= 0:
            return None
        return self.distance_m / self.seconds * 3.6

    @property
    def height_change(self) -> float | None:
        if self.entry_altitude is None or self.exit_altitude is None:
            return None
        return self.exit_altitude - self.entry_altitude


@dataclass(frozen=True)
class CompositeBest:
    """The fastest anyone covered each stretch, stitched end to end.

    A ceiling, not a plan: the stretches were flown by different pilots in
    different gliders at different times of day. Read it as "this much was
    available", and look at ``shares`` to see whether it is one pilot's flight
    with a few pieces borrowed or a genuine patchwork.
    """

    stretches: tuple[BestStretch, ...]
    winner_label: str | None
    winner_seconds: float | None
    covered_m: float = 0.0
    course_m: float = 0.0
    # The height the composite carried at each stretch boundary: one entry
    # more than there are stretches, opening height first. This is the
    # constraint made visible — and the thing to check when a composite looks
    # too good.
    altitudes: tuple[float, ...] = ()

    @property
    def distance_m(self) -> float:
        return sum(s.distance_m for s in self.stretches)

    @property
    def complete(self) -> bool:
        """Whether the search got all the way to the end of the course.

        It can run out: if every pilot who crossed a stretch entered it higher
        than the composite can be, there is no energy-consistent way on, and
        saying so is better than quietly stitching the rest anyway.
        """
        return self.course_m > 0 and self.covered_m >= self.course_m - 1.0

    @property
    def seconds(self) -> float:
        return sum(s.seconds for s in self.stretches)

    @property
    def speed_kmh(self) -> float | None:
        if self.seconds <= 0:
            return None
        return self.distance_m / self.seconds * 3.6

    @property
    def gain_on_winner_s(self) -> float | None:
        """How much the day's quickest pilot left on the table."""
        if self.winner_seconds is None:
            return None
        return self.winner_seconds - self.seconds

    @property
    def shares(self) -> list[tuple[str, int, float]]:
        """``(pilot, stretches, share of the task)``, biggest contributor first."""
        total = self.distance_m
        tally: dict[str, list[float]] = {}
        for stretch in self.stretches:
            entry = tally.setdefault(stretch.pilot, [0.0, 0.0])
            entry[0] += 1
            entry[1] += stretch.distance_m
        rows = [
            (pilot, int(count), (metres / total * 100.0) if total else 0.0)
            for pilot, (count, metres) in tally.items()
        ]
        return sorted(rows, key=lambda row: row[2], reverse=True)

    @property
    def gliders(self) -> list[str]:
        return sorted({s.glider for s in self.stretches if s.glider})

    @classmethod
    def build(
        cls,
        flights: list[FlightMetrics],
        segment_m: float = 10000.0,
    ) -> CompositeBest | None:
        """The quickest energy-consistent way through the course.

        A shortest-path search over ``(stretch, height)``. At each stretch the
        composite may fly any pilot's crossing of it, provided that pilot
        entered no higher than the composite currently is — you cannot borrow a
        glide that started above you — and it leaves having lost whatever that
        pilot lost, capped at the highest anyone reached there so it cannot
        bank height the sky never gave. Entering no lower than the contributor
        means leaving no lower either, so the composite stays within
        :data:`HEIGHT_TOLERANCE_M` of ground that somebody demonstrated was
        flyable; that tolerance is the only energy it is ever lent, and it is
        there because the heights are interpolated onto a stretch boundary
        rather than measured at one.

        A search rather than a greedy pass: the quickest crossing of a stretch
        is often the one that arrives lowest, and paying thirty seconds for
        height here is regularly what makes the next two stretches possible.
        Greedy cannot see that and neither can a per-stretch minimum.
        """
        tracks: list[tuple[ProgressTrack, FlightMetrics]] = []
        for metrics in flights:
            track = progress_track(metrics)
            if track is not None and len(track) >= 2:
                tracks.append((track, metrics))
        if len(tracks) < 2:
            return None

        # The span every pilot can be asked about. A progress track begins
        # where its pilot crossed the start line, which is a kilometre or two
        # along the course and not the same kilometre for everyone. Starting
        # the composite at zero would silently exclude the later-starting
        # tracks from the opening stretches — and the pilot excluded there is
        # often the quickest, which is how a "best possible" ends up slower
        # than somebody's actual flight.
        origin = max(track.distance_m[0] for track, _ in tracks)

        # How far the composite goes: as far as every finisher got, not as far
        # as the single deepest one did.
        #
        # Achieved distance stops a kilometre or two short of the turnpoint
        # centre at the finish, because the finish is a ring and a glider
        # crosses it wherever it crosses it. Two pilots who both finished can
        # therefore differ by a few hundred metres of "achieved" distance for
        # reasons that have nothing to do with racing — so bounding the span by
        # the outright maximum leaves exactly one pilot able to be compared
        # with the result, and it is whichever one happened to fly deepest into
        # the ring rather than whichever one was quickest.
        finishers = [track.max_distance_m for track, metrics in tracks if metrics.completed]
        if finishers:
            reach = min(finishers)
        else:
            reached = sorted((track.max_distance_m for track, _ in tracks), reverse=True)
            reach = reached[min(2, len(reached) - 1)]
        if reach - origin <= segment_m:
            return None

        # Every stretch, with everyone who crossed it and what it cost them.
        stages: list[list[BestStretch]] = []
        start = origin
        while start < reach - 1.0:
            end = min(start + segment_m, reach)
            options: list[BestStretch] = []
            for track, metrics in tracks:
                if track.max_distance_m < end:
                    continue
                entered, left = track.time_at(start), track.time_at(end)
                entry, exit_ = track.altitude_at(start), track.altitude_at(end)
                if entered is None or left is None or left <= entered:
                    continue
                if entry is None or exit_ is None:
                    continue
                options.append(
                    BestStretch(
                        start_m=start,
                        end_m=end,
                        seconds=left - entered,
                        pilot=metrics.flight.pilot.label,
                        glider=metrics.flight.pilot.glider_model,
                        flight_key=metrics.flight.flight_key,
                        path=tuple(tuple(point) for point in track.path_between(start, end)),
                        entry_altitude=entry,
                        exit_altitude=exit_,
                    )
                )
            if options:
                stages.append(options)
            start = end

        if not stages:
            return None

        # The best start anyone managed: the composite is allowed the same.
        opening = max(option.entry_altitude for option in stages[0])
        # The highest anyone reached on each stretch — the composite cannot
        # climb past a sky nobody got above.
        ceilings = [max(max(o.entry_altitude, o.exit_altitude) for o in stage) for stage in stages]

        # The search carries a *frontier* of (time so far, height) states and
        # keeps only those that nothing else beats on both counts at once.
        #
        # Rounding height into buckets would be the obvious way to keep the
        # state space small, and it is wrong here: the error compounds stage
        # after stage, and the composite quietly loses the few tens of metres
        # that were its margin over the pilot it is copying. A pilot then
        # becomes unusable for a reason that is pure arithmetic, and a "best
        # possible" comes out slower than a flight that actually happened. The
        # frontier holds exact heights instead, and stays small because being
        # both slower and lower than another state is what gets you dropped.
        Frontier = list[tuple[float, float, int, BestStretch | None]]
        frontier: Frontier = [(0.0, opening, -1, None)]
        history: list[Frontier] = [frontier]

        for index, stage in enumerate(stages):
            candidates: Frontier = []
            for position, (elapsed, height, _, _) in enumerate(frontier):
                for option in stage:
                    if height + HEIGHT_TOLERANCE_M < option.entry_altitude:
                        continue  # that glide started above us; we cannot borrow it
                    arrival = min(height - (option.entry_altitude - option.exit_altitude), ceilings[index])
                    candidates.append((elapsed + option.seconds, arrival, position, option))
            if not candidates:
                break

            # Quickest first; keep a state only if it is higher than everything
            # quicker than it. That is the Pareto frontier, exactly.
            candidates.sort(key=lambda row: (row[0], -row[1]))
            kept: Frontier = []
            highest = float("-inf")
            for candidate in candidates:
                if candidate[1] > highest + 1e-9:
                    kept.append(candidate)
                    highest = candidate[1]
            if len(kept) > MAX_FRONTIER:
                # Thin evenly, always keeping the quickest and the highest.
                step = len(kept) / (MAX_FRONTIER - 1)
                thinned = [kept[int(i * step)] for i in range(MAX_FRONTIER - 1)]
                thinned.append(kept[-1])
                kept = thinned
            frontier = kept
            history.append(frontier)

        if len(history) < 2:
            return None

        # The frontier is sorted by time, so the quickest way to the end is its
        # first entry whatever height it arrived at.
        chosen: list[BestStretch] = []
        heights: list[float] = []
        position = 0
        for level in range(len(history) - 1, 0, -1):
            _, height, previous, option = history[level][position]
            if option is None or previous < 0:  # pragma: no cover - defensive
                break
            chosen.append(option)
            heights.append(height)
            position = previous
        chosen.reverse()
        heights.reverse()
        if not chosen:
            return None
        heights.insert(0, opening)

        # Like for like: what the quickest pilot took over *this* span, not
        # over their whole task. The composite may have stopped short, and it
        # never covers the run-up to the start line that its span excludes.
        span_end = chosen[-1].end_m
        rivals = []
        for track, metrics in tracks:
            if track.max_distance_m < span_end:
                continue
            entered, left = track.time_at(origin), track.time_at(span_end)
            if entered is None or left is None:
                continue
            rivals.append((metrics.flight.pilot.label, left - entered))
        winner_label, winner_seconds = min(rivals, key=lambda row: row[1]) if rivals else (None, None)

        return cls(
            stretches=tuple(chosen),
            winner_label=winner_label,
            winner_seconds=winner_seconds,
            covered_m=span_end - origin,
            course_m=reach - origin,
            altitudes=tuple(heights),
        )
