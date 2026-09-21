"""Replay: a time cursor shared by the map and the barogram.

Streamlit re-runs the whole script on every interaction, so a slider-driven
animation would re-render the deck and the chart on every frame. That is
choppy at 1x and pointless at 20x, and it cannot express dragging on a chart at
all. The replay is therefore a self-contained page embedded with
``components.html``: the data crosses once, and play, scrub and speed are
handled in the browser.

This module is split so the part worth testing is testable: :func:`payload`
prepares plain data and knows nothing about HTML.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from debrief.core.metrics import FlightMetrics, fix_altitude

# Roughly how many samples each flight contributes. The plane's position is
# interpolated between them, so this sets payload size rather than smoothness:
# a four-hour flight lands near one sample per ten seconds.
TARGET_SAMPLES = 1500


@dataclass(frozen=True)
class ReplayTrack:
    label: str
    color: str
    t: list[int]  # timeline seconds
    lon: list[float]
    lat: list[float]
    alt: list[int]


def _thin(values: list, target: int) -> list:
    if len(values) <= target:
        return values
    step = len(values) // target + 1
    thinned = values[::step]
    if thinned[-1] is not values[-1]:
        thinned.append(values[-1])
    return thinned


def payload(
    flights: list[FlightMetrics],
    colors: list[str],
    align: str = "clock",
    basemap_style: str = "",
    target_samples: int = TARGET_SAMPLES,
) -> dict:
    """Prepare replay data.

    ``align`` chooses the timeline, matching the barogram's own control:
    ``clock`` puts every pilot on absolute time, so they move together as they
    really did; ``start`` measures from each pilot's own start, so flights that
    began far apart can still be watched side by side.

    Coordinates are rounded to five decimal places — about a metre, far finer
    than a glider's position means — which roughly halves the payload.
    """
    tracks: list[ReplayTrack] = []

    for index, metrics in enumerate(flights):
        fixes = _thin(metrics.flight.trace, target_samples)
        if len(fixes) < 2:
            continue

        aligned_on_start = align == "start" and metrics.start_time is not None
        origin = metrics.start_time if aligned_on_start else None

        times = []
        for fix in fixes:
            moment = fix["datetime"]
            seconds = (moment - origin).total_seconds() if origin is not None else moment.timestamp()
            times.append(round(seconds))

        tracks.append(
            ReplayTrack(
                label=metrics.flight.pilot.label,
                color=colors[index % len(colors)],
                t=times,
                lon=[round(f["lon"], 5) for f in fixes],
                lat=[round(f["lat"], 5) for f in fixes],
                alt=[round(fix_altitude(f)) for f in fixes],
            )
        )

    if not tracks:
        return {"tracks": [], "t0": 0, "t1": 0, "align": align, "style": basemap_style}

    return {
        "tracks": [track.__dict__ for track in tracks],
        "t0": min(track.t[0] for track in tracks),
        "t1": max(track.t[-1] for track in tracks),
        "align": align,
        "style": basemap_style,
        "bounds": [
            [
                min(min(track.lon) for track in tracks),
                min(min(track.lat) for track in tracks),
            ],
            [
                max(max(track.lon) for track in tracks),
                max(max(track.lat) for track in tracks),
            ],
        ],
    }


def payload_json(data: dict) -> str:
    """Compact JSON, safe to embed in a <script> block."""
    text = json.dumps(data, separators=(",", ":"))
    # A literal </script> inside the data would end the block early.
    return text.replace("</", "<\\/")


def render(
    flights: list[FlightMetrics],
    palette,
    basemap: str,
    align: str = "clock",
    height: int = 700,
) -> None:
    """Embed the replay for these flights."""
    import json
    from pathlib import Path

    import streamlit as st
    import streamlit.components.v1 as components

    from debrief.app.theme import BASEMAPS, basemap_raster

    if not flights:
        st.info("Nothing to replay.")
        return

    data = payload(
        flights,
        list(palette.series),
        align=align,
        basemap_style="",
    )
    if not data["tracks"]:
        st.info("These flights are too short to replay.")
        return

    template = (Path(__file__).parent / "replay_page.html").read_text(encoding="utf-8")
    page = (
        template.replace("__PAYLOAD__", payload_json(data))
        .replace(
            "__PALETTE__",
            json.dumps(
                {
                    "surface": palette.surface,
                    "ink": palette.ink_primary,
                    "muted": palette.ink_muted,
                    "grid": palette.grid,
                    "axis": palette.axis,
                }
            ),
        )
        .replace("__SURFACE__", palette.surface)
        .replace("__INK__", palette.ink_primary)
        .replace("__MUTED__", palette.ink_muted)
        .replace("__GRID__", palette.grid)
        .replace("__AXIS__", palette.axis)
        .replace("__TILES__", json.dumps(basemap_raster(basemap)))
        .replace("__ATTRIBUTION__", BASEMAPS[basemap]["attribution"])
    )
    components.html(page, height=height, scrolling=False)
