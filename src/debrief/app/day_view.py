"""The compare-a-day view: several pilots, one task, side by side."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import streamlit as st

from debrief.app import replay
from debrief.app.charts import comparison_barogram, leg_delta_chart
from debrief.app.maps import comparison_deck
from debrief.app.theme import Palette
from debrief.core.airspace import Airspace
from debrief.core.compare import DayComparison
from debrief.core.igc import IGCError, load_igc
from debrief.core.metrics import FlightMetrics, analyse_or_summarise
from debrief.sources.local import ArchivedDay, archived_days

# Colours come from the categorical palette, which holds eight slots that clear
# the colour-blind separation gates. Past that, pilots would share a colour and
# the legend would stop meaning anything.
MAX_COMPARED = 8


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    sign = "-" if seconds < 0 else ""
    total = int(abs(seconds))
    return f"{sign}{total // 60}:{total % 60:02d}"


def _num(value: float | None, digits: int = 1) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


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
        help=f"Up to {MAX_COMPARED}: past that pilots would share a colour.",
    )
    if not chosen:
        st.info("Pick at least one pilot.")
        return

    reference_label = st.selectbox(
        "Compare against",
        chosen,
        help="Leg deltas are measured against this pilot.",
    )
    align = st.radio(
        "Align barogram on",
        ["start", "clock"],
        horizontal=True,
        format_func=lambda mode: "each pilot's own start" if mode == "start" else "absolute clock time",
        help=(
            "Aligning on each pilot's start makes flights comparable when they "
            "started far apart; clock time shows what the sky was doing when."
        ),
    )

    selected = [labels[name] for name in chosen]
    day = DayComparison.build(selected, reference_key=labels[reference_label].flight.flight_key)
    for warning in day.warnings:
        st.warning(warning)

    st.caption(f"{archived.competition} · {archived.plane_class} · {archived.date:%d %B %Y}")
    if day.task_label:
        st.caption(f"Task: {day.task_label}")

    overview, replay_tab = st.tabs(["Overview", "Replay"])
    with replay_tab:
        replay.render(list(day.flights), palette, basemap, align=align)

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
