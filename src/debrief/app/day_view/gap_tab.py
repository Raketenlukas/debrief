"""Where the time went: one pilot's gap to a reference, along the course."""

from __future__ import annotations

import streamlit as st

from debrief.app.charts import progress_delta_chart
from debrief.app.day_view.tables import lost_table, swatch_legend
from debrief.app.format import gap
from debrief.app.maps import (
    PROGRESS_HEIGHT,
    PROGRESS_TIME,
    progress_deck,
    progress_thresholds,
)
from debrief.app.theme import Palette
from debrief.core.airspace import Airspace
from debrief.core.compare import DayComparison
from debrief.core.progress import (
    FIELD_BEST,
    FIELD_MEDIAN,
    SEGMENT_CHOICES_M,
    SEGMENT_M,
    ProgressComparison,
)


def render_progress(
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
    reference_options = [*list(by_label), FIELD_BEST, FIELD_MEDIAN]

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
        swatch_legend(palette.diverging.legend(soft, hard, unit)),
        unsafe_allow_html=True,
    )
    total = subject.final_delta_s
    st.caption(
        f"{subject.label} against {progress.reference_label}"
        + (f" — {gap(total)} over the task. " if total is not None else ". ")
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

    rows = lost_table(subject, progress.ruler, progress.reference_label)
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
