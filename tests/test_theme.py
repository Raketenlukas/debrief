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
