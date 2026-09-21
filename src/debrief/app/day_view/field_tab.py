"""The two views built from the whole class rather than from a selection."""

from __future__ import annotations

import streamlit as st

from debrief.app.day_view.tables import swatch_legend
from debrief.app.format import clock, gap
from debrief.app.maps import (
    CLIMB_HIGH_MS,
    CLIMB_LOW_MS,
    FIELD_CLIMBS,
    FIELD_LINES,
    composite_deck,
    field_deck,
)
from debrief.app.theme import Palette
from debrief.core.airspace import Airspace
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
from debrief.core.metrics import FlightMetrics
from debrief.core.progress import SEGMENT_CHOICES_M, SEGMENT_M


def render_field(
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
        swatch_legend(palette.sequential.legend(low, high, unit, digits)),
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


def render_composite(
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
    headline[1].metric("Composite time", clock(composite.seconds))
    if composite.winner_seconds is not None:
        headline[2].metric(
            f"Quickest pilot — {composite.winner_label.split(' — ')[0]}",
            clock(composite.winner_seconds),
        )
        headline[3].metric(
            "Left on the table",
            gap(composite.gain_on_winner_s),
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
            swatch_legend(palette.sequential.legend(min(speeds), max(speeds), " km/h", 0)),
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
