"""Palette invariants.

The slot ORDER is the colour-blind-safety mechanism, not decoration: the order
was chosen so that adjacent pairs clear the separation gates. Cycling it, or
reordering it casually, quietly breaks that property.
"""

import pytest

from debrief.app.theme import BASEMAPS, DARK, DEFAULT_BASEMAP, LIGHT, palette_for


@pytest.mark.parametrize("palette", [LIGHT, DARK], ids=["light", "dark"])
def test_the_first_slots_are_the_validated_set(palette):
    """Eight slots clear every separation check; the rest extend the palette for
    large fields and are explicitly not colour-alone reliable."""
    from debrief.app.theme import VALIDATED_SLOTS

    assert VALIDATED_SLOTS == 8
    assert len(palette.series) == 20
    assert len(set(palette.series[:VALIDATED_SLOTS])) == VALIDATED_SLOTS


@pytest.mark.parametrize("palette", [LIGHT, DARK], ids=["light", "dark"])
def test_leg_colours_are_never_reused_within_the_palette(palette):
    colours = [palette.leg_color(i) for i in range(len(palette.series))]
    assert len(set(colours)) == len(colours)


@pytest.mark.parametrize("palette", [LIGHT, DARK], ids=["light", "dark"])
def test_legs_beyond_the_palette_share_one_neutral_rather_than_cycling(palette):
    """A 7-leg task is ordinary; cycling would give two legs the same colour."""
    n = len(palette.series)
    assert palette.leg_color(n) == palette.overflow
    assert palette.leg_color(n + 3) == palette.overflow
    assert palette.overflow not in palette.series


@pytest.mark.parametrize("palette", [LIGHT, DARK], ids=["light", "dark"])
def test_spatial_series_is_the_all_pairs_safe_subset(palette):
    """Map paths are compared against every other path, not just neighbours."""
    assert len(palette.spatial_series) == 3
    assert palette.spatial_series == palette.series[:3]


def test_palette_selection():
    assert palette_for("dark") is DARK
    assert palette_for("light") is LIGHT
    assert palette_for(None) is LIGHT


def test_every_basemap_resolves_to_a_style():
    """A basemap is a MapLibre style, not a tile template: deck.gl's TileLayer
    fetches raster tiles and then silently drops them, which is what left the
    track floating on a blank page."""
    from debrief.app.theme import basemap_style

    assert DEFAULT_BASEMAP in BASEMAPS
    for name, source in BASEMAPS.items():
        assert source["attribution"].strip(), name
        style = basemap_style(name)
        # Every basemap is a raster source wrapped in a style document: the two
        # maps must show the same thing, and CARTO's raster tiles now need an
        # account even though their vector styles do not.
        assert style.startswith("data:application/json,"), name
        assert "%7B%22version%22%3A%208" in style, name  # {"version": 8
        assert "raster" in source, name


def test_every_basemap_also_offers_raster_tiles():
    """The replay draws its own map on a canvas, so it needs image tiles — a
    vector style is no use to it."""
    from debrief.app.theme import basemap_raster

    for name in BASEMAPS:
        raster = basemap_raster(name)
        assert raster.startswith("https://"), name
        assert all(token in raster for token in ("{z}", "{x}", "{y}")), name


def test_an_unknown_basemap_falls_back_rather_than_raising_in_the_replay():
    from debrief.app.theme import basemap_raster

    assert basemap_raster("no such map") == BASEMAPS[DEFAULT_BASEMAP]["raster"]


def test_raster_basemaps_keep_their_attribution_in_the_style():
    """MapLibre shows the source's attribution; dropping it would strip credit."""
    from urllib.parse import unquote

    from debrief.app.theme import basemap_style

    style = unquote(basemap_style("Terrain (OpenTopoMap)"))
    assert "OpenTopoMap" in style
    assert "OpenStreetMap" in style


# --- the diverging scale -----------------------------------------------------
#
# A categorical palette is checked by its slot order; a diverging one is checked
# by whether its two arms can be told apart. The maths below is the same
# Machado-Oliveira-Fernandes 2009 simulation and OKLab ΔE the palette was
# stepped with, re-implemented here so the claim in the docstring is a test
# rather than a note — a "small adjustment" to one hex is exactly the change
# that would quietly collapse the two arms.

_MACHADO = {
    "protan": (
        (0.152286, 1.052583, -0.204868),
        (0.114503, 0.786281, 0.099216),
        (-0.003882, -0.048116, 1.051998),
    ),
    "deutan": (
        (0.367322, 0.860646, -0.227968),
        (0.280085, 0.672501, 0.047413),
        (-0.011820, 0.042940, 0.968881),
    ),
}


def _linear(hex_color):
    raw = hex_color.lstrip("#")
    channels = [int(raw[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    return [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]


def _oklab(rgb):
    r, g, b = rgb
    long_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    medium = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    short = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (
        0.2104542553 * long_ + 0.7936177850 * medium - 0.0040720468 * short,
        1.9779984951 * long_ - 2.4285922050 * medium + 0.4505937099 * short,
        0.0259040371 * long_ + 0.7827717662 * medium - 0.8086757660 * short,
    )


def _simulate(rgb, kind):
    matrix = _MACHADO[kind]
    return [min(max(sum(m * c for m, c in zip(row, rgb, strict=True)), 0.0), 1.0) for row in matrix]


def _delta_e(first, second, kind=None):
    """Euclidean distance in OKLab ×100 — the units every threshold here uses."""
    a = _oklab(_simulate(_linear(first), kind) if kind else _linear(first))
    b = _oklab(_simulate(_linear(second), kind) if kind else _linear(second))
    return 100 * sum((x - y) ** 2 for x, y in zip(a, b, strict=True)) ** 0.5


def _relative_luminance(hex_color):
    r, g, b = _linear(hex_color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(first, second):
    high, low = sorted((_relative_luminance(first), _relative_luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize("palette", [LIGHT, DARK], ids=["light", "dark"])
def test_the_two_arms_stay_apart_under_colour_blindness(palette):
    """Which side of the reference you were on is the whole message."""
    scale = palette.diverging
    for gain, loss, floor in (
        (scale.gain_strong, scale.loss_strong, 15.0),
        (scale.gain, scale.loss, 10.0),
    ):
        assert _delta_e(gain, loss) >= floor
        for kind in ("protan", "deutan"):
            assert _delta_e(gain, loss, kind) >= floor * 0.66, (kind, gain, loss)


@pytest.mark.parametrize("palette", [LIGHT, DARK], ids=["light", "dark"])
def test_every_step_survives_being_drawn_on_a_map(palette):
    """These are lines over terrain, not fills on the chart surface: a step
    that fades into the page would also fade into a field."""
    scale = palette.diverging
    steps = [scale.gain_strong, scale.gain, scale.neutral, scale.loss, scale.loss_strong]
    for step in steps:
        assert _contrast(step, palette.surface) >= 3.0, step


@pytest.mark.parametrize("palette", [LIGHT, DARK], ids=["light", "dark"])
def test_the_midpoint_is_grey_and_the_poles_are_not(palette):
    """A hue at the midpoint would invent a third category out of 'no difference'."""
    scale = palette.diverging
    _, a, b = _oklab(_linear(scale.neutral))
    assert (a**2 + b**2) ** 0.5 < 0.02
    for pole in (scale.gain_strong, scale.loss_strong):
        _, a, b = _oklab(_linear(pole))
        assert (a**2 + b**2) ** 0.5 > 0.10


@pytest.mark.parametrize("palette", [LIGHT, DARK], ids=["light", "dark"])
def test_the_poles_are_the_categorical_blue_and_red(palette):
    """Deliberate, and only safe because of how the scale is used.

    The documented diverging pair is this palette's own blue and red, so the two
    poles *are* slots 1 and 8. That would be a collision on any chart showing
    identity and polarity at once — which is why the map that uses this scale
    draws one pilot and a grey reference, and never a coloured field. The
    middle steps are the scale's own, shared with nothing.
    """
    scale = palette.diverging
    assert scale.gain_strong == palette.series[0]
    assert scale.loss_strong == palette.series[7]
    assert scale.gain not in palette.series
    assert scale.loss not in palette.series


@pytest.mark.parametrize("palette", [LIGHT, DARK], ids=["light", "dark"])
def test_positive_means_worse_on_both_sides_of_the_scale(palette):
    scale = palette.diverging
    assert scale.color(30.0, 5.0, 20.0) == scale.loss_strong
    assert scale.color(10.0, 5.0, 20.0) == scale.loss
    assert scale.color(0.0, 5.0, 20.0) == scale.neutral
    assert scale.color(-10.0, 5.0, 20.0) == scale.gain
    assert scale.color(-30.0, 5.0, 20.0) == scale.gain_strong
    # Exactly on a threshold counts as the stronger band, and an unmeasurable
    # stretch is neutral rather than an arbitrary side.
    assert scale.color(20.0, 5.0, 20.0) == scale.loss_strong
    assert scale.color(None, 5.0, 20.0) == scale.neutral


@pytest.mark.parametrize("palette", [LIGHT, DARK], ids=["light", "dark"])
def test_the_legend_names_every_step_with_its_threshold(palette):
    entries = palette.diverging.legend(5.0, 20.0, " s")
    assert len(entries) == 5
    assert [colour for colour, _ in entries] == [
        palette.diverging.gain_strong,
        palette.diverging.gain,
        palette.diverging.neutral,
        palette.diverging.loss,
        palette.diverging.loss_strong,
    ]
    assert all("s" in label for _, label in entries)
    assert "ahead" in entries[0][1] and "behind" in entries[-1][1]
