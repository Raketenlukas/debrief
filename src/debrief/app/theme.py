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

# How many categorical slots clear every separation check. Beyond this the
# palette still works, but 20 categories cannot all be told apart: the best
# achievable worst pair measures ΔE 11.1 against a floor of 15, so at large
# field sizes colour narrows the search and the legend, the hover text and the
# comparison table are what actually identify a track.
VALIDATED_SLOTS = 8


@dataclass(frozen=True)
class DivergingScale:
    """Polarity against a reference: worse on one side, better on the other.

    Blue and red around a grey midpoint — the documented diverging pair, chosen
    because the two poles read as opposites and the midpoint reads as "nothing
    in it". A single hue could not say which side of the reference a pilot was
    on, and a rainbow would invent an order that the numbers do not have.

    The arms are steps from each pole toward the grey, matched in lightness, so
    the scale varies by hue and saturation rather than by brightness: these
    colours are drawn on a map over arbitrary terrain, where a pale step
    vanishes over a light field and a dark one vanishes over forest. Every step
    holds at least 3.5:1 against the chart surface.

    Three bands per arm, no more. A finer ramp measures under ΔE 4 between its
    near-neutral steps and the midpoint, which no one can see, and under
    colour-blind simulation the red arm collapses toward grey sooner than the
    blue one does — at the middle step, ΔE 4.7 (deuteranopia, light). So colour
    is never the only channel here: callers also scale the line width with the
    magnitude and print the number in the tooltip and the table.
    """

    gain_strong: str
    gain: str
    neutral: str
    loss: str
    loss_strong: str

    def color(self, loss: float | None, soft: float, hard: float) -> str:
        """Colour for a signed value where **positive means worse**.

        ``soft`` is where a difference starts being worth seeing and ``hard``
        is where it is serious. Both are in the caller's own units — seconds
        for a time gap, metres for a height one — because what counts as a big
        difference is a property of the quantity, not of the palette.
        """
        if loss is None:
            return self.neutral
        if loss >= hard:
            return self.loss_strong
        if loss >= soft:
            return self.loss
        if loss <= -hard:
            return self.gain_strong
        if loss <= -soft:
            return self.gain
        return self.neutral

    def legend(self, soft: float, hard: float, unit: str) -> list[tuple[str, str]]:
        """The scale as labelled swatches, so the map is never read by guesswork."""
        return [
            (self.gain_strong, f"{hard:.0f}{unit} or more ahead"),
            (self.gain, f"{soft:.0f}-{hard:.0f}{unit} ahead"),
            (self.neutral, f"within {soft:.0f}{unit}"),
            (self.loss, f"{soft:.0f}-{hard:.0f}{unit} behind"),
            (self.loss_strong, f"{hard:.0f}{unit} or more behind"),
        ]


@dataclass(frozen=True)
class SequentialRamp:
    """Magnitude: one hue, stepped light to dark, no polarity implied.

    Used where a number has a floor and no meaningful middle — how strong the
    climbs were in a cell, how many gliders crossed it, how fast a stretch of
    course went. A diverging scale would invent a midpoint these have no reason
    to have, and a rainbow would invent an order out of hue.

    Five steps from the documented blue ramp, stepped monotonically in
    lightness (adjacent ΔL ≥ 0.09 against a floor of 0.06). Dark mode flips the
    anchor rather than the hue: the end nearest the surface is the pale one on
    a dark page and the dark one on a light page, so "near the floor" always
    means "receding into the background". Both ends sit at the ramp's
    documented ordinal bounds, where the surface-nearest step still clears
    2:1 (2.06 light, 2.15 dark) — these are fills with an outline and a
    labelled legend, not colour-alone marks.
    """

    steps: tuple[str, ...]

    def color(self, value: float | None, low: float, high: float) -> str:
        """Colour for a value, clamped to ``[low, high]``."""
        if value is None or high <= low:
            return self.steps[0]
        fraction = min(max((value - low) / (high - low), 0.0), 1.0)
        index = min(int(fraction * len(self.steps)), len(self.steps) - 1)
        return self.steps[index]

    def legend(self, low: float, high: float, unit: str, digits: int = 1) -> list[tuple[str, str]]:
        out = []
        width = (high - low) / len(self.steps)
        for index, step in enumerate(self.steps):
            start = low + index * width
            end = start + width
            if index == len(self.steps) - 1:
                out.append((step, f"{start:.{digits}f}{unit} and up"))
            else:
                out.append((step, f"{start:.{digits}f}-{end:.{digits}f}{unit}"))
        return out


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
    diverging: DivergingScale
    sequential: SequentialRamp

    def leg_color(self, index: int) -> str:
        """Colour follows the leg, never its rank, so filtering never repaints.

        Slots are never cycled: a task with more legs than the palette has slots
        would otherwise give two legs the same colour and silently make the
        legend ambiguous. Legs past the last slot share one neutral instead,
        which reads as "beyond the palette" rather than as a specific leg.

        The first :data:`VALIDATED_SLOTS` clear every separation check; the rest
        are as far apart as 20 categories can be, which is not far enough to
        rely on colour alone.
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
        # Slots 1-8 are the validated set and pass every separation check.
        "#2a78d6",  # blue
        "#eb6834",  # orange
        "#1baf7a",  # aqua
        "#eda100",  # yellow
        "#e87ba4",  # magenta
        "#008300",  # green
        "#4a3aa7",  # violet
        "#e34948",  # red
        # Slots 9-20 extend the set for large fields. See VALIDATED_SLOTS.
        "#79c316",
        "#a81982",
        "#8ca9f1",
        "#ba0d01",
        "#229ebc",
        "#d43b88",
        "#a09037",
        "#b364e9",
        "#8d5403",
        "#ea76eb",
        "#a75464",
        "#845ea2",
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
    # Stepped from the categorical blue and red toward the muted grey in OKLab
    # (40% and 78% of the way), so the arms are lightness-matched by
    # construction. Full poles measure ΔE 32.3 apart in normal vision, 21.6
    # under protanopia, 27.3 under deuteranopia; the middle pair 17.7 / 11.7 /
    # 14.7.
    diverging=DivergingScale(
        gain_strong="#2a78d6",
        gain="#5c82b2",
        neutral="#898781",
        loss="#bf6c64",
        loss_strong="#e34948",
    ),
    # Blue ramp steps 250 -> 650: light end nearest the page.
    sequential=SequentialRamp(("#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281")),
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
        "#79c316",
        "#8831b3",
        "#37c695",
        "#ba032c",
        "#46bcd7",
        "#8d5403",
        "#ee75e6",
        "#83862e",
        "#bb49bd",
        "#4b5ea2",
        "#e18db0",
        "#8e4771",
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
    # The same construction against the dark surface: poles ΔE 29.0 apart in
    # normal vision, 19.2 protan, 24.9 deutan.
    diverging=DivergingScale(
        gain_strong="#3987e5",
        gain="#638ab9",
        neutral="#898781",
        loss="#bf7973",
        loss_strong="#e66767",
    ),
    # Blue ramp steps 600 -> 200: the anchor flips, so the dark end is the one
    # nearest the page here.
    sequential=SequentialRamp(("#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4")),
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


# Base maps are raster tile templates, from providers that need no API key.
#
# Both maps use the same source: the overview wraps it in a MapLibre style
# document, and the replay draws the tiles onto a canvas itself. CARTO's vector
# styles look better but their raster endpoints now require an account, and a
# basemap that renders in one view and asks for a key in the other is worse
# than a plainer one that works in both.
#
# A deck.gl TileLayer cannot draw raster tiles on its own either: its default
# renderSubLayers builds a GeoJsonLayer, so a PNG is fetched and then silently
# dropped. The basemap belongs to the map, not to a layer.
BASEMAPS: dict[str, dict[str, str]] = {
    "Streets (Esri)": {
        "raster": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
        "attribution": "© Esri, HERE, Garmin, OpenStreetMap contributors",
    },
    "Topographic (Esri)": {
        "raster": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
        "attribution": "© Esri, HERE, Garmin, USGS, OpenStreetMap contributors",
    },
    "Terrain (OpenTopoMap)": {
        "raster": "https://a.tile.opentopomap.org/{z}/{x}/{y}.png",
        "attribution": "Map data: © OpenStreetMap contributors, SRTM | Style: © OpenTopoMap (CC-BY-SA)",
    },
    "Satellite (Esri)": {
        "raster": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attribution": "© Esri, Maxar, Earthstar Geographics",
    },
    "swisstopo (CH only)": {
        # No API key: access is granted by Referer and is free on localhost. A
        # public deployment needs a WMTS account from swisstopo.
        "raster": "https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.pixelkarte-farbe/default/current/3857/{z}/{x}/{y}.jpeg",
        "attribution": "© swisstopo",
    },
}

DEFAULT_BASEMAP = "Streets (Esri)"


def raster_style(url: str, attribution: str, background: str = "#e8eef5") -> str:
    """Wrap a raster tile template in a MapLibre style, as a data URL.

    MapLibre takes a style document; deck.gl's JSON bridge can only carry a
    string. Encoding the document as a data URL keeps raster basemaps available
    without a tile-rendering layer.
    """
    import json
    from urllib.parse import quote

    style = {
        "version": 8,
        "sources": {
            "raster-source": {
                "type": "raster",
                "tiles": [url],
                "tileSize": 256,
                "attribution": attribution,
            }
        },
        "layers": [
            {"id": "background", "type": "background", "paint": {"background-color": background}},
            {"id": "raster-layer", "type": "raster", "source": "raster-source"},
        ],
    }
    return "data:application/json," + quote(json.dumps(style))


def basemap_raster(name: str) -> str:
    """The raster tile template for a basemap.

    The replay draws its map on a canvas rather than through a map library, so
    it needs image tiles. Every entry carries one, including the vector styles.
    """
    source = BASEMAPS.get(name)
    if source is None or "raster" not in source:
        return BASEMAPS[DEFAULT_BASEMAP]["raster"]
    return source["raster"]


def basemap_style(name: str) -> str:
    """The MapLibre style for a named basemap.

    A value that is already a style URL passes through, so a custom or
    self-hosted style can be used without editing this table.
    """
    source = BASEMAPS.get(name)
    if source is None:
        if name.startswith(("http://", "https://", "data:")):
            return name
        raise KeyError(f"unknown basemap {name!r}; expected one of {sorted(BASEMAPS)}")
    return raster_style(source["raster"], source["attribution"])


def palette_for(theme_base: str | None) -> Palette:
    return DARK if (theme_base or "light").lower() == "dark" else LIGHT
