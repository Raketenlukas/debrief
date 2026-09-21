"""deck.gl maps, driven from ``pydeck``.

deck.gl rather than Leaflet because an IGC file is tens of thousands of points,
and because the same layer model carries over to a React front end later —
pydeck now, deck.gl in the browser then, with the layer definitions essentially
unchanged.

One module per question the maps answer:

``base``
    projection, colour conversion, view framing, the shared tooltip
``layers``
    the pieces more than one map draws: the task, airspace, the flown track
``flight``
    one flight in detail, and a field of them side by side
``gap``
    one pilot's track coloured by what each stretch cost against a reference
``field``
    the whole class pooled: where the lift was, where they flew, and the best
    route those two made possible

This module is the public surface. Importing from ``debrief.app.maps`` keeps
working exactly as it did when all of this was one file.
"""

from debrief.app.maps.base import GEOD, TILE_SIZE, TOOLTIP, hex_to_rgb, sector_ring, view_state
from debrief.app.maps.field import (
    CLIMB_HIGH_MS,
    CLIMB_LOW_MS,
    FIELD_CLIMBS,
    FIELD_LINES,
    composite_deck,
    field_deck,
)
from debrief.app.maps.flight import comparison_deck, flight_deck
from debrief.app.maps.gap import (
    HEIGHT_HARD_M,
    HEIGHT_SOFT_M,
    PROGRESS_HEIGHT,
    PROGRESS_TIME,
    TIME_HARD_S_PER_KM,
    TIME_SOFT_S_PER_KM,
    progress_deck,
    progress_thresholds,
    segment_loss,
)
from debrief.app.maps.layers import airspace_layers, task_layers, track_layers

__all__ = [
    "CLIMB_HIGH_MS",
    "CLIMB_LOW_MS",
    "FIELD_CLIMBS",
    "FIELD_LINES",
    "GEOD",
    "HEIGHT_HARD_M",
    "HEIGHT_SOFT_M",
    "PROGRESS_HEIGHT",
    "PROGRESS_TIME",
    "TILE_SIZE",
    "TIME_HARD_S_PER_KM",
    "TIME_SOFT_S_PER_KM",
    "TOOLTIP",
    "airspace_layers",
    "comparison_deck",
    "composite_deck",
    "field_deck",
    "flight_deck",
    "hex_to_rgb",
    "progress_deck",
    "progress_thresholds",
    "sector_ring",
    "segment_loss",
    "task_layers",
    "track_layers",
    "view_state",
]
