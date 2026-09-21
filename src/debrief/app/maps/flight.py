"""The two identity maps: one flight in detail, and a field side by side."""

from __future__ import annotations

import pydeck as pdk

from debrief.app.maps.base import TOOLTIP, hex_to_rgb, view_state
from debrief.app.maps.layers import airspace_layers, task_layers, track_layers
from debrief.app.theme import Palette, basemap_style
from debrief.core.airspace import Airspace
from debrief.core.compare import DayComparison
from debrief.core.metrics import FlightMetrics


def flight_deck(
    metrics: FlightMetrics,
    palette: Palette,
    basemap: str,
    airspaces: list[Airspace] | None = None,
) -> pdk.Deck:
    """Build the map: basemap, airspace, declared task, flown track."""
    layers: list[pdk.Layer] = []
    # Order is paint order: airspace under the task, task under the track, so
    # the flight is never hidden by what it was flying through.
    if airspaces:
        layers.extend(airspace_layers(airspaces, palette))
    if metrics.task is not None:
        layers.extend(task_layers(metrics.task, palette))
    layers.extend(track_layers(metrics, palette))

    return pdk.Deck(
        layers=layers,
        initial_view_state=view_state(metrics.flight.trace),
        # The basemap is the map's own style, not a layer. map_provider="carto"
        # selects a keyless provider; map_provider=None switches the basemap off
        # entirely, which is what left the track floating on a blank page.
        map_provider="carto",
        map_style=basemap_style(basemap),
        tooltip=TOOLTIP,
    )


def comparison_deck(
    day: DayComparison,
    palette: Palette,
    basemap: str,
    airspaces: list[Airspace] | None = None,
) -> pdk.Deck:
    """Several pilots' tracks on one map, one colour each.

    Each track is a single path rather than the hoverable segments of the
    single-flight map: with a field on screen the question is which line is
    whose and where they diverged, not what the vario read at 14:32. The
    single-flight view keeps the detail.
    """
    layers: list[pdk.Layer] = []
    if airspaces:
        layers.extend(airspace_layers(airspaces, palette))
    if day.flights and day.flights[0].task is not None:
        layers.extend(task_layers(day.flights[0].task, palette))

    tracks = []
    for index, metrics in enumerate(day.flights):
        speed = metrics.task_speed_kmh
        tracks.append(
            {
                "path": [[f["lon"], f["lat"]] for f in metrics.flight.trace],
                "color": hex_to_rgb(palette.leg_color(index), 225),
                "info": (
                    f"{metrics.flight.pilot.label}\n"
                    + (f"{speed:.1f} km/h on task" if speed else "no task speed")
                ),
            }
        )

    if tracks:
        layers.append(
            pdk.Layer(
                "PathLayer",
                data=tracks,
                get_path="path",
                get_color="color",
                get_width=60,
                width_min_pixels=2,
                width_max_pixels=4,
                pickable=True,
                auto_highlight=True,
            )
        )

    # Frame every track, not just the first: pilots diverge, and a view fitted
    # to one of them cuts the others off exactly where it got interesting.
    all_fixes = [fix for metrics in day.flights for fix in metrics.flight.trace]
    view = view_state(all_fixes) if all_fixes else pdk.ViewState(latitude=0, longitude=0, zoom=1)

    return pdk.Deck(
        layers=layers,
        initial_view_state=view,
        map_provider="carto",
        map_style=basemap_style(basemap),
        tooltip=TOOLTIP,
    )
