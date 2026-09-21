"""The whole field's day: where the lift was, where they flew, and the best
route the two of them made possible."""

from __future__ import annotations

import pydeck as pdk

from debrief.app.maps.base import TOOLTIP, hex_to_rgb, view_state
from debrief.app.maps.layers import airspace_layers, task_layers
from debrief.app.theme import Palette, basemap_style
from debrief.core.airspace import Airspace
from debrief.core.field import BestStretch, Cell, CompositeBest
from debrief.core.models import TaskDef

FIELD_CLIMBS = "climbs"
FIELD_LINES = "lines"

# Climb rates are bounded by physics and by what a competition day looks like:
# under 0.5 m/s nobody stays, over 4 m/s is a very good day anywhere in Europe.
# A fixed range rather than the day's own min and max, so a weak day looks weak
# instead of being restretched into the same picture as a strong one.
CLIMB_LOW_MS = 0.5
CLIMB_HIGH_MS = 4.0


def _cell_layers(
    cells: list[Cell],
    palette: Palette,
    low: float,
    high: float,
    value_of,
    info_of,
) -> list[pdk.Layer]:
    """A grid of squares, filled on the sequential ramp.

    Filled polygons rather than a smooth heatmap. A heatmap blurs *how many*
    into *how strong* — two mediocre climbs glow like one good one — and the
    question here is which of the two you are looking at. Squares keep the
    aggregate honest and let the tooltip carry the count.
    """
    if not cells:
        return []

    data = []
    for cell in cells:
        value = value_of(cell)
        colour = hex_to_rgb(palette.sequential.color(value, low, high), 190)
        data.append(
            {
                "polygon": [list(point) for point in cell.polygon],
                "fill": colour,
                "info": info_of(cell),
            }
        )

    return [
        pdk.Layer(
            "PolygonLayer",
            data=data,
            get_polygon="polygon",
            filled=True,
            stroked=True,
            get_fill_color="fill",
            # The pale end of the ramp sits at 2:1 on the page by design; over
            # a satellite tile it needs an edge to be a shape at all.
            get_line_color=hex_to_rgb(palette.ink_muted, 90),
            line_width_min_pixels=1,
            pickable=True,
            auto_highlight=True,
        )
    ]


def _climb_info(cell: Cell) -> str:
    return (
        f"{cell.mean:.1f} m/s average over {cell.count} climb"
        f"{'' if cell.count == 1 else 's'}\n"
        f"best {cell.best:.1f} m/s · {cell.pilot_count} pilot"
        f"{'' if cell.pilot_count == 1 else 's'} stopped here"
    )


def _lines_info(cell: Cell) -> str:
    return (
        f"{cell.pilot_count} pilot{'' if cell.pilot_count == 1 else 's'} crossed here\n"
        f"{cell.count} samples · typically {cell.median:.0f} m"
    )


def field_deck(
    cells: list[Cell],
    task: TaskDef | None,
    palette: Palette,
    basemap: str,
    mode: str = FIELD_CLIMBS,
    airspaces: list[Airspace] | None = None,
) -> pdk.Deck:
    """The whole field's day as a grid: where the lift was, or where they flew."""
    layers: list[pdk.Layer] = []
    if airspaces:
        layers.extend(airspace_layers(airspaces, palette))

    if mode == FIELD_CLIMBS:
        layers.extend(
            _cell_layers(cells, palette, CLIMB_LOW_MS, CLIMB_HIGH_MS, lambda c: c.mean, _climb_info)
        )
    else:
        # Density has no natural ceiling, so the range comes from the day: the
        # busiest cell is the top of the scale and everything reads against it.
        busiest = max((c.pilot_count for c in cells), default=1)
        layers.extend(
            _cell_layers(cells, palette, 1.0, max(float(busiest), 2.0), lambda c: c.pilot_count, _lines_info)
        )

    # The task on top of the grid, so "where the lift was" can be read against
    # "where you had to go" — which is the whole tactical question.
    if task is not None:
        layers.extend(task_layers(task, palette))

    corners = [{"lat": lat, "lon": lon} for cell in cells for lon, lat in (cell.polygon[0], cell.polygon[2])]
    view = view_state(corners) if corners else pdk.ViewState(latitude=0, longitude=0, zoom=1)
    return pdk.Deck(
        layers=layers,
        initial_view_state=view,
        map_provider="carto",
        map_style=basemap_style(basemap),
        tooltip=TOOLTIP,
    )


def _stretch_info(stretch: BestStretch) -> str:
    speed = stretch.speed_kmh
    climb = stretch.height_change
    lines = [
        f"km {stretch.start_m / 1000:.0f}–{stretch.end_m / 1000:.0f} of the course",
        f"{stretch.pilot}" + (f" · {stretch.glider}" if stretch.glider else ""),
        f"{speed:.0f} km/h" if speed else "no speed",
    ]
    if climb is not None:
        lines.append(f"{climb:+.0f} m over the stretch")
    return "\n".join(lines)


def composite_deck(
    composite: CompositeBest,
    palette: Palette,
    basemap: str,
    airspaces: list[Airspace] | None = None,
    task: TaskDef | None = None,
) -> pdk.Deck:
    """The day's ceiling: the fastest crossing of every stretch, stitched up.

    Coloured by speed, not by pilot. Colour spent on identity would cap the
    useful field at three — any two stretches can end up side by side on a map,
    which is the all-pairs case — and would answer "who" at the cost of "where
    was the day quick", which is the question a route is asked. The contributor
    is in the tooltip and in the table below it.

    The joins between stretches are left visible. Two consecutive stretches
    flown by different pilots do not meet, and drawing a line between them
    would manufacture a flight that nobody made.
    """
    speeds = [s.speed_kmh for s in composite.stretches if s.speed_kmh]
    low = min(speeds) if speeds else 0.0
    high = max(speeds) if speeds else 1.0

    layers: list[pdk.Layer] = []
    if airspaces:
        layers.extend(airspace_layers(airspaces, palette))
    if task is not None:
        layers.extend(task_layers(task, palette))

    paths = [
        {
            "path": [list(point) for point in stretch.path],
            "color": hex_to_rgb(palette.sequential.color(stretch.speed_kmh, low, high), 235),
            "info": _stretch_info(stretch),
        }
        for stretch in composite.stretches
        if len(stretch.path) >= 2
    ]
    if paths:
        layers.append(
            pdk.Layer(
                "PathLayer",
                data=paths,
                get_path="path",
                get_color="color",
                get_width=110,
                width_min_pixels=3,
                width_max_pixels=8,
                pickable=True,
                auto_highlight=True,
            )
        )

    # Where the baton changes hands. These are the joins that make the
    # composite a composite rather than a flight.
    handovers = []
    for before, after in zip(composite.stretches, composite.stretches[1:], strict=False):
        if before.pilot == after.pilot or not after.path:
            continue
        handovers.append(
            {
                "position": list(after.path[0]),
                "info": f"km {after.start_m / 1000:.0f}: {before.pilot} → {after.pilot}",
            }
        )
    if handovers:
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                data=handovers,
                get_position="position",
                get_fill_color=hex_to_rgb(palette.surface, 255),
                get_line_color=hex_to_rgb(palette.ink_secondary, 255),
                get_radius=200,
                radius_min_pixels=4,
                radius_max_pixels=8,
                stroked=True,
                filled=True,
                line_width_min_pixels=2,
                pickable=True,
            )
        )

    corners = [{"lat": lat, "lon": lon} for stretch in composite.stretches for lon, lat in stretch.path]
    view = view_state(corners) if corners else pdk.ViewState(latitude=0, longitude=0, zoom=1)
    return pdk.Deck(
        layers=layers,
        initial_view_state=view,
        map_provider="carto",
        map_style=basemap_style(basemap),
        tooltip=TOOLTIP,
    )
