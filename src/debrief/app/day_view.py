"""The compare-a-day view: several pilots, one task, side by side."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import streamlit as st

from debrief.app import replay
from debrief.app.charts import comparison_barogram, leg_delta_chart, progress_delta_chart
from debrief.app.maps import (
    PROGRESS_HEIGHT,
    PROGRESS_TIME,
    comparison_deck,
    progress_deck,
    progress_thresholds,
)
from debrief.app.theme import VALIDATED_SLOTS, Palette
from debrief.core.airspace import Airspace
from debrief.core.compare import DayComparison
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

    controls = st.columns([2, 2, 2, 2])
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
    with controls[3]:
        segment_m = st.select_slider(
            "Stretch length",
            options=list(SEGMENT_CHOICES_M),
            value=SEGMENT_M,
            format_func=lambda metres: f"{metres / 1000:.0f} km",
            help=(
                "How much course each coloured piece covers. Two pilots almost "
                "never climb in the same place, so at 2 km you mostly see who "
                "stopped where: red where you climbed, blue where the reference "
                "did. That is the resolution for finding the one thermal that "
                "cost the day. Ten kilometres holds a climb and the glide it "
                "buys, which is the comparison that answers who was quicker."
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

    overview, lost_tab, replay_tab = st.tabs(["Overview", "Where the time went", "Replay"])
    with replay_tab:
        replay.render(list(day.flights), palette, basemap, align=align)

    with lost_tab:
        _render_progress(day, palette, basemap, airspaces, align=align)

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
