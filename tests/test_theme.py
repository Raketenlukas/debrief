"""Palette invariants.

The slot ORDER is the colour-blind-safety mechanism, not decoration: the order
was chosen so that adjacent pairs clear the separation gates. Cycling it, or
reordering it casually, quietly breaks that property.
"""

import pytest

from debrief.app.theme import BASEMAPS, DARK, DEFAULT_BASEMAP, LIGHT, palette_for


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
        if "raster" in source:
            # A raster source is wrapped in a style document, carried inline.
            assert style.startswith("data:application/json,"), name
            assert "{z}" in source["raster"], name
            assert "%7B%22version%22%3A%208" in style, name  # {"version": 8
        else:
            assert style.startswith("https://"), name
            assert style.endswith("style.json"), name


def test_raster_basemaps_keep_their_attribution_in_the_style():
    """MapLibre shows the source's attribution; dropping it would strip credit."""
    from urllib.parse import unquote

    from debrief.app.theme import basemap_style

    style = unquote(basemap_style("Terrain (OpenTopoMap)"))
    assert "OpenTopoMap" in style
    assert "OpenStreetMap" in style
