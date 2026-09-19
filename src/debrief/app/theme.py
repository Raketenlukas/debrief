"""Chart palette and tile sources.

Colours come from a validated categorical palette. Only the first three
categorical slots are used, which is the set that clears the colour-blind
separation checks under an all-pairs comparison — relevant here because leg
colours appear together on a map and in a scatter, not just as adjacent bars.

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
    cruise: str
    thermal: str
    thermal_band: str

    def leg_color(self, index: int) -> str:
        """Colour follows the leg, never its rank, so filtering never repaints."""
        return self.series[index % len(self.series)]


LIGHT = Palette(
    surface="#fcfcfb",
    page="#f9f9f7",
    ink_primary="#0b0b0b",
    ink_secondary="#52514e",
    ink_muted="#898781",
    grid="#e1e0d9",
    axis="#c3c2b7",
    series=("#2a78d6", "#eb6834", "#1baf7a"),
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
    series=("#3987e5", "#d95926", "#199e70"),
    cruise="#3987e5",
    thermal="#d95926",
    thermal_band="rgba(217, 89, 38, 0.18)",
)


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
    "Esri World Imagery": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attribution": "© Esri, Maxar, Earthstar Geographics",
    },
}

DEFAULT_TILE_SOURCE = "OpenTopoMap"


def palette_for(theme_base: str | None) -> Palette:
    return DARK if (theme_base or "light").lower() == "dark" else LIGHT
