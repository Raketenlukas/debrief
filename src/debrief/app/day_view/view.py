"""The compare-a-day view: several pilots, one task, side by side."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from debrief.app import replay
from debrief.app.charts import comparison_barogram, leg_delta_chart
from debrief.app.day_view.archive import analyse_day, pick_day
from debrief.app.day_view.field_tab import render_composite, render_field
from debrief.app.day_view.gap_tab import render_progress
from debrief.app.day_view.tables import comparison_table
from debrief.app.maps import comparison_deck
from debrief.app.theme import VALIDATED_SLOTS, Palette
from debrief.core.airspace import Airspace
from debrief.core.compare import DayComparison

# The palette holds twenty slots. The first eight clear every colour-blind
# separation check; the rest are as far apart as twenty categories can be,
# which is not far enough to rely on colour alone — hence the note below the
# picker, and why the comparison table carries every number as text.
MAX_COMPARED = 20


def render(archive_root: Path, palette: Palette, basemap: str, airspaces: list[Airspace]) -> None:
    """Draw the whole comparison view."""
    archived = pick_day(archive_root)
    if archived is None:
        return

    paths = tuple(str(p) for p in archived.paths)
    mtimes = tuple(p.stat().st_mtime for p in archived.paths)
    all_metrics = analyse_day(paths, mtimes)
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
        render_progress(day, palette, basemap, airspaces, align=align)

    # Both of these pool the *whole* day, not the selection: a map of the lift
    # built from the four pilots you happened to tick is a map of four pilots.
    with field_tab:
        render_field(all_metrics, palette, basemap, airspaces)

    with best_tab:
        render_composite(all_metrics, palette, basemap, airspaces)

    with overview:
        st.pydeck_chart(comparison_deck(day, palette, basemap, airspaces), use_container_width=True)
        st.plotly_chart(comparison_barogram(day, palette, align=align), use_container_width=True)
        if len(day.flights) > 1:
            st.plotly_chart(leg_delta_chart(day, palette), use_container_width=True)

    st.markdown("#### Comparison")
    st.dataframe(comparison_table(day), use_container_width=True, hide_index=True)
    st.caption(
        "Ranks and percentiles are against the pilots selected above, not the whole field. "
        "'pct' columns are the share of that selection beaten."
    )
