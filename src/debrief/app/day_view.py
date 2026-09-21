"""The compare-a-day view: several pilots, one task, side by side."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import streamlit as st

from debrief.app import replay
from debrief.app.charts import comparison_barogram, leg_delta_chart, progress_delta_chart
from debrief.app.maps import (
    CLIMB_HIGH_MS,
    CLIMB_LOW_MS,
    FIELD_CLIMBS,
    FIELD_LINES,
    PROGRESS_HEIGHT,
    PROGRESS_TIME,
    comparison_deck,
    composite_deck,
    field_deck,
    progress_deck,
    progress_thresholds,
)
from debrief.app.theme import VALIDATED_SLOTS, Palette
from debrief.core.airspace import Airspace
from debrief.core.compare import DayComparison
from debrief.core.field import (
    CELL_CHOICES_M,
    CLIMB_MIN_GAIN_M,
    CLIMB_MIN_SECONDS,
    DEFAULT_CELL_M,
    CompositeBest,
    climb_grid,
    cruise_grid,
    field_climbs,
    field_cruise,
)
from debrief.core.igc import IGCError, load_igc
from debrief.core.metrics import FlightMetrics, analyse_or_summarise
from debrief.core.progress import (
    FIELD_BEST,
    FIELD_MEDIAN,
    SEGMENT_CHOICES_M,
    SEGMENT_M,
    PilotProgress,
    ProgressComparison,
)
from debrief.sources.local import ArchivedDay, archived_days

# The palette holds twenty slots. The first eight clear every colour-blind
# separation check; the rest are as far apart as twenty categories can be,
# which is not far enough to rely on colour alone — hence the note below the
# picker, and why the comparison table carries every number as text.
MAX_COMPARED = 20


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    sign = "-" if seconds < 0 else ""
    total = int(abs(seconds))
    return f"{sign}{total // 60}:{total % 60:02d}"


def _num(value: float | None, digits: int = 1) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _elapsed(seconds: float | None) -> str:
    """Time into the task, as a clock reads it.

    ``_duration`` counts minutes and seconds, which is right for a gap and
    wrong past an hour: an hour and a half into the task shows as "90:00" and
    reads as ninety hours.
    """
    if seconds is None:
        return "—"
    total = int(seconds)
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


@st.cache_data(show_spinner=False)
def _analyse_day(paths: tuple[str, ...], mtimes: tuple[float, ...]) -> list[FlightMetrics]:
    """Analyse a whole day. Cached on paths and mtimes, since this is the slow part."""
    del mtimes
    out = []
    for path in paths:
        try:
            out.append(analyse_or_summarise(load_igc(path)))
        except (IGCError, ValueError) as exc:  # noqa: PERF203
            st.warning(f"{Path(path).name}: {exc}")
    return out


def _comparison_table(day: DayComparison) -> list[dict]:
    """One row per pilot, covering all four comparison families.

    Ranks and percentiles are computed against this selection, not the whole
    field: comparing yourself with the three people you chose is the question
    being asked.
    """
    speed = day.distribution("task_speed_kmh", higher_is_better=True)
    climb = day.distribution("average_climb_ms", higher_is_better=True)
    glide = day.distribution("glide_ratio", higher_is_better=True)
    detour = day.distribution("detour_percent", higher_is_better=False)

    rows = []
    for comparison in day.comparisons():
        metrics = comparison.metrics
        rows.append(
            {
                "Pilot": metrics.flight.pilot.label,
                "km/h": _num(metrics.task_speed_kmh),
                "Rank": speed.rank_of(metrics.task_speed_kmh) or "—",
                "Δ min:s": _duration(comparison.total_delta_s),
                # Start tactics
                "Start": f"{metrics.start_time:%H:%M}" if metrics.start_time else "—",
                "Start m": _num(metrics.start_altitude, 0),
                "→1st climb": _duration(metrics.time_to_first_climb_s),
                # Climb quality
                "Climb m/s": _num(metrics.average_climb_ms, 2),
                "Climb pct": _num(climb.percentile_of(metrics.average_climb_ms), 0),
                "Best m/s": _num(metrics.best_climb_ms, 2),
                "Circling %": _num(metrics.percent_circling, 0),
                "Band m": _num(metrics.climbing_altitude_mean, 0),
                # Cruise efficiency
                "Cruise km/h": _num(metrics.cruise_speed_kmh, 0),
                "L/D": _num(metrics.glide_ratio),
                "L/D pct": _num(glide.percentile_of(metrics.glide_ratio), 0),
                "Detour %": _num(metrics.detour_percent),
                "Detour pct": _num(detour.percentile_of(metrics.detour_percent), 0),
            }
        )
    return rows


def _swatch_legend(entries: list[tuple[str, str]]) -> str:
    """The colour scale as labelled swatches.

    A diverging scale is unreadable without one: the reader has to know which
    end is which, and no amount of intuition supplies the thresholds.
    """
    boxes = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:6px;margin-right:16px;">'
        f'<span style="width:22px;height:10px;border-radius:2px;background:{colour};'
        f'display:inline-block;"></span><span>{label}</span></span>'
        for colour, label in entries
    )
    return f'<div style="font-size:12px;opacity:0.85;margin:4px 0 10px;">{boxes}</div>'


def _lost_table(pilot: PilotProgress, ruler, reference_label: str, count: int = 8) -> list[dict]:
    """The stretches of course that cost the most, in order, with when and where.

    The map shows where; this says exactly when, and puts a number on it. It is
    also the relief channel for the map's colours: the two near-neutral steps of
    a diverging scale are hard to separate under colour-blindness, so the
    finding must be readable without them.
    """
    rows = []
    for rank, segment in enumerate(pilot.ranked_segments(worst_first=True)[:count], start=1):
        height = segment.height_delta_m
        rows.append(
            {
                "#": rank,
                "From": f"{segment.start.time:%H:%M:%S}",
                "To": f"{segment.end.time:%H:%M:%S}",
                "Into task": _elapsed(segment.start.elapsed_s),
                "Where": ruler.describe(segment.end.distance_m),
                "Lost s": f"{segment.lost_s:+.0f}",
                "Took": _duration(segment.seconds),
                f"m vs {reference_label.split(' — ')[0]}": "—" if height is None else f"{height:+.0f}",
            }
        )
    return rows


def _render_progress(
    day: DayComparison,
    palette: Palette,
    basemap: str,
    airspaces: list[Airspace],
    align: str,
) -> None:
    """Where the time went: the gap to a reference, along the task."""
    if len(day.flights) < 2:
        st.info("Pick at least two pilots — a gap needs someone to be a gap to.")
        return

    by_label = {m.flight.pilot.label: m for m in day.flights}
    reference_options = list(by_label) + [FIELD_BEST, FIELD_MEDIAN]

    controls = st.columns([3, 3, 2])
    with controls[0]:
        subject_label = st.selectbox(
            "Whose flight",
            list(by_label),
            help="The track drawn on the map, coloured by what each stretch of course cost.",
        )
    with controls[1]:
        reference_choice = st.selectbox(
            "Measured against",
            reference_options,
            index=len(by_label) if len(by_label) > 1 else 0,
            format_func=lambda key: {
                FIELD_BEST: "the field's best (nobody flew it)",
                FIELD_MEDIAN: "the field's median",
            }.get(key, key),
            help=(
                "One rival, or the whole selection at once. The field's best is "
                "the quickest anyone got to each point on the course — a ceiling, "
                "not a flight."
            ),
        )
    with controls[2]:
        mode = st.radio(
            "Colour the track by",
            [PROGRESS_TIME, PROGRESS_HEIGHT],
            horizontal=True,
            format_func=lambda key: "time" if key == PROGRESS_TIME else "height",
            help=(
                "Time is what the day is scored on; height at the same point on "
                "task is usually the cause of which lost time is the symptom."
            ),
        )
    # A row of its own, and buttons rather than a slider. Three named values is
    # a choice, not a range: squeezed into a quarter-width column, a three-stop
    # slider is a 240-pixel track whose handle has to be dragged to hit one of
    # three positions, which is a fiddly way to answer a question with three
    # answers.
    segment_m = st.radio(
        "Stretch length — how much course each coloured piece covers",
        list(SEGMENT_CHOICES_M),
        index=list(SEGMENT_CHOICES_M).index(SEGMENT_M),
        horizontal=True,
        format_func=lambda metres: f"{metres / 1000:.0f} km",
        help=(
            "Two pilots almost never climb in the same place, so at 2 km you "
            "mostly see who stopped where: red where you climbed, blue where "
            "the reference did. That is the resolution for finding the one "
            "thermal that cost the day, and it says little about who was "
            "quicker. Ten kilometres holds a climb and the glide it buys, "
            "which is the comparison that answers that question."
        ),
    )

    is_field = reference_choice in (FIELD_BEST, FIELD_MEDIAN)
    reference = reference_choice if is_field else by_label[reference_choice]
    progress = ProgressComparison.build(list(day.flights), reference, align=align, segment_m=segment_m)
    if progress is None:
        st.info("These flights carry no usable task progress, so there is nothing to measure.")
        return
    for warning in progress.warnings:
        st.warning(warning)

    subject = progress.for_key(by_label[subject_label].flight.flight_key)
    if subject is None:
        st.info(f"{subject_label} has no task progress to measure.")
        return
    if subject.is_reference:
        st.info("That pilot *is* the reference — pick someone else to measure, or change the reference.")
        return

    soft, hard, unit = progress_thresholds(mode, segment_m)
    st.pydeck_chart(
        progress_deck(
            progress,
            subject,
            palette,
            basemap,
            mode=mode,
            airspaces=airspaces,
            segment_m=segment_m,
        ),
        use_container_width=True,
    )
    st.markdown(
        _swatch_legend(palette.diverging.legend(soft, hard, unit)),
        unsafe_allow_html=True,
    )
    total = subject.final_delta_s
    st.caption(
        f"{subject.label} against {progress.reference_label}"
        + (f" — {_duration(total)} over the task. " if total is not None else ". ")
        + (
            f"Colour and thickness are the seconds gained or lost over each "
            f"{segment_m / 1000:.0f} km of course"
            if mode == PROGRESS_TIME
            else "Colour and thickness are the height difference at the same point on the course"
        )
        + "; the ringed marks are the five worst. Hover anything for the numbers."
    )

    if len(progress.pilots) > 1:
        st.plotly_chart(progress_delta_chart(progress, palette), use_container_width=True)
        st.caption(
            "Every rise is time lost at the kilometre it was lost at, and the flat "
            "stretches are pilots progressing at the same rate. The saw-tooth is "
            "real and not noise: it is two gliders climbing in different places, "
            "each one dropping behind while the other runs on. What it costs is "
            "whatever the line has not given back by the finish."
        )

    rows = _lost_table(subject, progress.ruler, progress.reference_label)
    if rows:
        st.markdown("#### The stretches that cost the most")
        st.dataframe(rows, use_container_width=True, hide_index=True)
        st.caption(
            f"Each row is {segment_m / 1000:.0f} km of course. 'Lost s' is the time "
            "this pilot took over that stretch minus the time the reference took over "
            "the same stretch, so the rows count the same seconds the leg deltas do, "
            "at a finer resolution than a whole leg. A stretch that took far longer "
            "than its neighbours is a stretch with a climb in it."
        )


def _render_field(
    flights: list[FlightMetrics],
    palette: Palette,
    basemap: str,
    airspaces: list[Airspace],
) -> None:
    """The day as the whole field saw it: where the lift was, where they flew."""
    if len(flights) < 2:
        st.info("One flight is not a field. Import the rest of the class to build this.")
        return

    controls = st.columns([3, 3])
    with controls[0]:
        mode = st.radio(
            "Show",
            [FIELD_CLIMBS, FIELD_LINES],
            horizontal=True,
            format_func=lambda key: "where the lift was" if key == FIELD_CLIMBS else "where they flew",
            help=(
                "Two different maps from the same day. The lift map pools every "
                "climb the field found; the route map pools the flying between "
                "them, with the circling taken out so thermals do not draw as "
                "traffic."
            ),
        )
    with controls[1]:
        cell_m = st.radio(
            "Cell size",
            list(CELL_CHOICES_M),
            index=list(CELL_CHOICES_M).index(DEFAULT_CELL_M),
            horizontal=True,
            format_func=lambda metres: f"{metres / 1000:.1f} km".replace(".0 km", " km"),
            help=(
                "Finer cells resolve a single thermal but split one thermal "
                "found by two pilots an hour apart into two cells, because it "
                "drifted in between."
            ),
        )

    only_shared = st.checkbox(
        "Only where more than one pilot went",
        value=False,
        help=(
            "One pilot's detour is not a feature of the day. Ticking this drops "
            "every cell that a single pilot visited, which is the difference "
            "between a map of the weather and a map of one flight."
        ),
    )

    climbs = field_climbs(flights)
    if mode == FIELD_CLIMBS:
        cells = list(climb_grid(list(climbs), cell_m=cell_m))
        low, high, unit, digits = CLIMB_LOW_MS, CLIMB_HIGH_MS, " m/s", 1
    else:
        cells = list(cruise_grid(list(field_cruise(flights)), cell_m=cell_m))
        busiest = max((c.pilot_count for c in cells), default=1)
        low, high, unit, digits = 1.0, max(float(busiest), 2.0), " pilots", 0

    if only_shared:
        cells = [c for c in cells if c.pilot_count > 1]

    if not cells:
        st.info("Nothing to grid — no climbs were detected in these flights.")
        return

    task = next((m.task for m in flights if m.task is not None), None)
    st.pydeck_chart(
        field_deck(cells, task, palette, basemap, mode=mode, airspaces=airspaces),
        use_container_width=True,
    )
    st.markdown(
        _swatch_legend(palette.sequential.legend(low, high, unit, digits)),
        unsafe_allow_html=True,
    )

    if mode == FIELD_CLIMBS:
        rates = sorted(c.climb_ms for c in climbs)
        best = max(climbs, key=lambda c: c.climb_ms) if climbs else None
        summary = st.columns(4)
        summary[0].metric("Climbs in the field", f"{len(climbs)}")
        summary[1].metric("Median climb", f"{rates[len(rates) // 2]:.2f} m/s" if rates else "—")
        summary[2].metric("Best climb", f"{best.climb_ms:.2f} m/s" if best else "—")
        summary[3].metric("Cells with lift", f"{len(cells)}")
        st.caption(
            "Each cell is shaded by the *average* climb found in it, and says in "
            "its tooltip how many climbs and how many pilots that average rests "
            "on. A dark cell visited once is one pilot's good luck; a dark cell "
            "visited by eight is where the day was. Climbs under "
            f"{CLIMB_MIN_GAIN_M:.0f} m or {CLIMB_MIN_SECONDS:.0f} s are left out — "
            "the phase detector counts a bump taken in a turn as a climb, and by "
            "number those would swamp the decisions a pilot actually made."
        )
    else:
        st.caption(
            "Shaded by how many *different* pilots crossed each cell, not by how "
            "many fixes landed in it, so a slow glider does not outvote a fast "
            "one. Circling is excluded: this is the map of where the field "
            "chose to go, and the empty ground beside the motorway is part of "
            "the answer."
        )


def _render_composite(
    flights: list[FlightMetrics],
    palette: Palette,
    basemap: str,
    airspaces: list[Airspace],
) -> None:
    """The fastest crossing of every stretch, stitched into one route."""
    if len(flights) < 2:
        st.info("A composite needs a field to draw from. Import the rest of the class.")
        return

    segment_m = st.radio(
        "Stretch length — the pieces the composite is built from",
        list(SEGMENT_CHOICES_M),
        index=list(SEGMENT_CHOICES_M).index(SEGMENT_M),
        horizontal=True,
        format_func=lambda metres: f"{metres / 1000:.0f} km",
        help=(
            "Shorter pieces take the best of more pilots and make a faster, "
            "less flyable composite: at 2 km it will hop between gliders every "
            "couple of minutes. Longer pieces stay closer to something a single "
            "pilot could have flown."
        ),
    )

    composite = CompositeBest.build(flights, segment_m=segment_m)
    if composite is None:
        st.info("Not enough flights with task progress to build a composite.")
        return

    headline = st.columns(4)
    speed = composite.speed_kmh
    headline[0].metric(
        "Composite speed",
        f"{speed:.1f} km/h" if speed else "—",
        help="Over the same stretch of course the times below are measured on.",
    )
    headline[1].metric("Composite time", _elapsed(composite.seconds))
    if composite.winner_seconds is not None:
        headline[2].metric(
            f"Quickest pilot — {composite.winner_label.split(' — ')[0]}",
            _elapsed(composite.winner_seconds),
        )
        headline[3].metric(
            "Left on the table",
            _duration(composite.gain_on_winner_s),
            help=(
                "How much quicker the composite is than the quickest single "
                "flight. A small number means somebody already flew most of "
                "what the day had; a large one means the day was won and lost "
                "in pieces."
            ),
        )
    else:
        headline[2].metric("Pilots it borrows from", f"{len(composite.shares)}")

    st.pydeck_chart(
        composite_deck(
            composite,
            palette,
            basemap,
            airspaces=airspaces,
            task=next((m.task for m in flights if m.task is not None), None),
        ),
        use_container_width=True,
    )
    speeds = [s.speed_kmh for s in composite.stretches if s.speed_kmh]
    if speeds:
        st.markdown(
            _swatch_legend(palette.sequential.legend(min(speeds), max(speeds), " km/h", 0)),
            unsafe_allow_html=True,
        )

    st.warning(
        "This is a ceiling, not a plan. Its pieces were flown by "
        f"{len(composite.shares)} pilots"
        + (f" in {len(composite.gliders)} types of glider" if len(composite.gliders) > 1 else "")
        + " at different times of day — nobody could have flown it, and the "
        "ringed joins are where it changes hands. Read it as what the day had "
        "in it, and read the gap to your own time as the size of the prize, not "
        "as a list of your mistakes."
    )

    st.markdown("#### Who supplied what")
    st.dataframe(
        [
            {
                "Pilot": pilot,
                "Stretches": stretches,
                "Share of task": f"{share:.0f}%",
            }
            for pilot, stretches, share in composite.shares
        ],
        use_container_width=True,
        hide_index=True,
    )
    st.caption(
        "A composite dominated by one name is a day somebody simply flew better. "
        "A composite spread across ten is a day where the winner was the pilot "
        "who strung together the most of what was available."
    )


def _pick_day(archive_root: Path) -> ArchivedDay | None:
    days = archived_days(archive_root)
    if not days:
        st.info(
            "No competition days in the archive yet. Import one with "
            "**Import a SoaringSpot day** in the sidebar — it downloads every "
            "competitor's flight, which is what makes a comparison possible."
        )
        return None

    competitions = sorted({d.competition for d in days})
    competition = st.sidebar.selectbox("Competition", competitions)
    in_competition = [d for d in days if d.competition == competition]

    classes = sorted({d.plane_class for d in in_competition})
    plane_class = st.sidebar.selectbox("Class", classes)
    in_class = [d for d in in_competition if d.plane_class == plane_class]

    dates: list[dt.date] = sorted({d.date for d in in_class}, reverse=True)
    date = st.sidebar.selectbox("Day", dates, format_func=lambda d: f"{d:%a %d %b %Y}")

    for day in in_class:
        if day.date == date:
            return day
    return None


def render(archive_root: Path, palette: Palette, basemap: str, airspaces: list[Airspace]) -> None:
    """Draw the whole comparison view."""
    archived = _pick_day(archive_root)
    if archived is None:
        return

    paths = tuple(str(p) for p in archived.paths)
    mtimes = tuple(p.stat().st_mtime for p in archived.paths)
    all_metrics = _analyse_day(paths, mtimes)
    if not all_metrics:
        st.error("None of the files in that day could be analysed.")
        return

    day_all = DayComparison.build(all_metrics)
    if not day_all.flights:
        st.error("No flights in that day carry a task, so there is nothing to compare.")
        return

    labels = {m.flight.pilot.label: m for m in day_all.flights}
    chosen = st.multiselect(
        "Pilots",
        list(labels),
        default=list(labels)[: min(4, len(labels))],
        max_selections=MAX_COMPARED,
        help=f"Up to {MAX_COMPARED} pilots.",
    )
    if not chosen:
        st.info("Pick at least one pilot.")
        return
    if len(chosen) > VALIDATED_SLOTS:
        st.caption(
            f"Past {VALIDATED_SLOTS} pilots some colours become hard to tell apart, "
            "especially with colour-blindness. Hover a track, or read the table below."
        )

    reference_label = st.selectbox(
        "Compare against",
        chosen,
        help="Leg deltas are measured against this pilot.",
    )
    align = st.radio(
        "Align on",
        ["start", "clock"],
        horizontal=True,
        format_func=lambda mode: "each pilot's own start" if mode == "start" else "absolute clock time",
        help=(
            "Aligning on each pilot's start makes flights comparable when they "
            "started far apart; clock time shows what the sky was doing when. "
            "Drives the barogram and the time-lost analysis alike."
        ),
    )

    selected = [labels[name] for name in chosen]
    day = DayComparison.build(selected, reference_key=labels[reference_label].flight.flight_key)
    for warning in day.warnings:
        st.warning(warning)

    st.caption(f"{archived.competition} · {archived.plane_class} · {archived.date:%d %B %Y}")
    if day.task_label:
        st.caption(f"Task: {day.task_label}")

    overview, lost_tab, field_tab, best_tab, replay_tab = st.tabs(
        ["Overview", "Where the time went", "The day's map", "The best that was there", "Replay"]
    )
    with replay_tab:
        replay.render(list(day.flights), palette, basemap, align=align)

    with lost_tab:
        _render_progress(day, palette, basemap, airspaces, align=align)

    # Both of these pool the *whole* day, not the selection: a map of the lift
    # built from the four pilots you happened to tick is a map of four pilots.
    with field_tab:
        _render_field(all_metrics, palette, basemap, airspaces)

    with best_tab:
        _render_composite(all_metrics, palette, basemap, airspaces)

    with overview:
        st.pydeck_chart(comparison_deck(day, palette, basemap, airspaces), use_container_width=True)
        st.plotly_chart(comparison_barogram(day, palette, align=align), use_container_width=True)
        if len(day.flights) > 1:
            st.plotly_chart(leg_delta_chart(day, palette), use_container_width=True)

    st.markdown("#### Comparison")
    st.dataframe(_comparison_table(day), use_container_width=True, hide_index=True)
    st.caption(
        "Ranks and percentiles are against the pilots selected above, not the whole field. "
        "'pct' columns are the share of that selection beaten."
    )
