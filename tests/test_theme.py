"""Palette invariants.

The slot ORDER is the colour-blind-safety mechanism, not decoration: the order
was chosen so that adjacent pairs clear the separation gates. Cycling it, or
reordering it casually, quietly breaks that property.
"""

import pytest

from debrief.app.theme import DARK, DEFAULT_TILE_SOURCE, LIGHT, TILE_SOURCES, palette_for


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


def test_every_tile_source_is_usable():
    assert DEFAULT_TILE_SOURCE in TILE_SOURCES
    for name, source in TILE_SOURCES.items():
        assert source["url"].startswith("https://"), name
        assert "{z}" in source["url"] and "{x}" in source["url"] and "{y}" in source["url"], name
        assert source["attribution"].strip(), name
