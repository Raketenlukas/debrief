"""Chart palette and tile sources.

Colours come from a validated categorical palette, in a fixed slot order that is
the colour-blind-safety mechanism rather than a cosmetic choice.

Two pairlists matter. Bars and lines only ever place *adjacent* slots side by
side, and all eight slots clear the separation gates on that pairlist. Marks
compared against every other mark — scattered points, spatial paths on a map —
need the stricter all-pairs gate, which only the first three slots clear. Hence
``series`` (eight, for leg-coloured bars) and ``spatial_series`` (three).

Three light-mode slots sit below 3:1 contrast on the light surface; the leg
table is the required relief, so it is not optional UI.

Dark mode is a selected set of steps for the dark surface, not an automatic
inversion of the light one.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    surface: str
    page: str
    ink_primary: str
    ink_secondary: str
    ink_muted: str
    grid: str
    axis: str
    series: tuple[str, ...]
    spatial_series: tuple[str, ...]
    overflow: str
    airspace: dict[str, str]
    cruise: str
    thermal: str
    thermal_band: str

    def leg_color(self, index: int) -> str:
        """Colour follows the leg, never its rank, so filtering never repaints.

        Slots are never cycled: a task with more legs than the palette has slots
        would otherwise give two legs the same colour and silently make the
        legend ambiguous. Legs past the last slot share one neutral instead,
        which reads as "beyond the palette" rather than as a specific leg.
        """
        if index < len(self.series):
            return self.series[index]
        return self.overflow


LIGHT = Palette(
    surface="#fcfcfb",
    page="#f9f9f7",
    ink_primary="#0b0b0b",
    ink_secondary="#52514e",
    ink_muted="#898781",
    grid="#e1e0d9",
    axis="#c3c2b7",
    series=(
        "#2a78d6",  # blue
        "#eb6834",  # orange
        "#1baf7a",  # aqua
        "#eda100",  # yellow
        "#e87ba4",  # magenta
        "#008300",  # green
        "#4a3aa7",  # violet
        "#e34948",  # red
    ),
    spatial_series=("#2a78d6", "#eb6834", "#1baf7a"),
    overflow="#898781",
    airspace={
        # Restriction severity, not class letter: what a pilot needs off a
        # glance is "may I be here", and the class is in the tooltip anyway.
        # These are the reserved status steps, never reused for a data series.
        "restricted": "#d03b3b",
        "controlled": "#2a78d6",
        "wave": "#0ca30c",
        "other": "#898781",
    },
    cruise="#2a78d6",
    thermal="#eb6834",
    thermal_band="rgba(235, 104, 52, 0.13)",
)

DARK = Palette(
    surface="#1a1a19",
    page="#0d0d0d",
    ink_primary="#ffffff",
    ink_secondary="#c3c2b7",
    ink_muted="#898781",
    grid="#2c2c2a",
    axis="#383835",
    series=(
        "#3987e5",  # blue
        "#d95926",  # orange
        "#199e70",  # aqua
        "#c98500",  # yellow
        "#d55181",  # magenta
        "#008300",  # green
        "#9085e9",  # violet
        "#e66767",  # red
    ),
    spatial_series=("#3987e5", "#d95926", "#199e70"),
    overflow="#898781",
    airspace={
        "restricted": "#d03b3b",
        "controlled": "#3987e5",
        "wave": "#0ca30c",
        "other": "#898781",
    },
    cruise="#3987e5",
    thermal="#d95926",
    thermal_band="rgba(217, 89, 38, 0.18)",
)


# OpenAIR class letters grouped by what they mean for a glider pilot. Unknown
# classes fall through to "other" rather than being dropped, so a country file
# using a local code still draws.
AIRSPACE_FAMILY: dict[str, str] = {
    # Keep out, or ask first.
    "P": "restricted",
    "R": "restricted",
    "Q": "restricted",
    "GP": "restricted",
    "TRA": "restricted",
    "TSA": "restricted",
    "PROHIBITED": "restricted",
    "RESTRICTED": "restricted",
    "DANGER": "restricted",
    # Controlled: entry needs a clearance.
    "A": "controlled",
    "B": "controlled",
    "C": "controlled",
    "D": "controlled",
    "CTR": "controlled",
    "CTA": "controlled",
    "TMA": "controlled",
    # A wave window is a permission, not a restriction.
    "W": "wave",
    "GSEC": "wave",
}


def airspace_family(airspace_class: str | None, airspace_type: str | None = None) -> str:
    for value in (airspace_class, airspace_type):
        if value and value.strip().upper() in AIRSPACE_FAMILY:
            return AIRSPACE_FAMILY[value.strip().upper()]
    return "other"


# Base maps. Terrain matters far more than roads for soaring, so the default is a
# topographic style rather than standard OSM carto — which is also the tile
# service the OSM Foundation asks applications not to consume.
TILE_SOURCES: dict[str, dict[str, str]] = {
    "OpenTopoMap": {
        "url": "https://a.tile.opentopomap.org/{z}/{x}/{y}.png",
        "attribution": "Map data: © OpenStreetMap contributors, SRTM | Style: © OpenTopoMap (CC-BY-SA)",
    },
    "swisstopo (CH only)": {
        # No API key: access is granted by Referer and is free on localhost. A
        # public deployment needs a WMTS account from swisstopo.
        "url": "https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.pixelkarte-farbe/default/current/3857/{z}/{x}/{y}.jpeg",
        "attribution": "© swisstopo",
    },
    "CARTO Voyager (streets)": {
        # Roads, towns and labels, rendered from OpenStreetMap data. Keyless,
        # unlike most vector-tile hosts, and it carries the place-name detail a
        # topographic style leaves out.
        "url": "https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png",
        "attribution": "© OpenStreetMap contributors, © CARTO",
    },
    "Esri World Imagery": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attribution": "© Esri, Maxar, Earthstar Geographics",
    },
}

DEFAULT_TILE_SOURCE = "OpenTopoMap"


def palette_for(theme_base: str | None) -> Palette:
    return DARK if (theme_base or "light").lower() == "dark" else LIGHT
