"""Streamlit debrief for a single flight.

Run with::

    streamlit run src/debrief/app/main.py

The app is a thin shell: it picks a flight, calls ``analyse``, and renders. All
the soaring lives in ``debrief.core``, so the same analysis backs a CLI today and
a web API later without being rewritten.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import streamlit as st

# Allow `streamlit run src/debrief/app/main.py` from a checkout without an install.
if __package__ is None or __package__ == "":  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from debrief.app.charts import barogram, climb_profile  # noqa: E402
from debrief.app.maps import flight_deck  # noqa: E402
from debrief.app.theme import DEFAULT_TILE_SOURCE, TILE_SOURCES, palette_for  # noqa: E402
from debrief.core.igc import IGCError, load_igc  # noqa: E402
from debrief.core.metrics import FlightMetrics, analyse  # noqa: E402
from debrief.sources.local import LocalArchive  # noqa: E402

DEFAULT_ARCHIVE = Path("data/igc")


def _fmt(value: float | None, spec: str = ".1f", suffix: str = "") -> str:
    """Never print a formatted None; an unmeasurable metric shows as an em dash."""
    return "—" if value is None else f"{value:{spec}}{suffix}"


def _duration(seconds: float | None) -> str:
    if not seconds:
        return "—"
    total = int(seconds)
    return f"{total // 3600}:{(total % 3600) // 60:02d}:{total % 60:02d}"


@st.cache_data(show_spinner=False)
def _analyse_path(path: str, mtime: float) -> FlightMetrics:
    """Cache on (path, mtime) so editing a file busts the entry."""
    del mtime
    return analyse(load_igc(path))


def _pick_flight() -> Path | None:
    st.sidebar.header("Flight")

    uploaded = st.sidebar.file_uploader("Upload an IGC file", type=["igc"])
    if uploaded is not None:
        temp_dir = Path(tempfile.gettempdir()) / "debrief-uploads"
        temp_dir.mkdir(exist_ok=True)
        target = temp_dir / uploaded.name
        target.write_bytes(uploaded.getvalue())
        return target

    root = Path(st.sidebar.text_input("…or an archive directory", str(DEFAULT_ARCHIVE)))
    paths = list(LocalArchive(root).archive_paths())
    if not paths:
        st.sidebar.caption(f"No .igc files under `{root}`.")
        return None

    return st.sidebar.selectbox("Flight", paths, format_func=lambda p: str(p.relative_to(root)))


def _header(metrics: FlightMetrics) -> None:
    flight = metrics.flight
    st.subheader(flight.pilot.label)
    bits = [f"{flight.date:%d %B %Y}", metrics.task.label]
    if flight.pilot.glider_model:
        bits.insert(1, flight.pilot.glider_model)
    if flight.competition:
        bits.append(flight.competition.competition)
    st.caption(" · ".join(bits))

    if metrics.outlanded:
        st.warning(f"Outlanding — {len(metrics.legs)} of {metrics.task.n_legs} legs flown.")
    for warning in metrics.warnings:
        if not warning.startswith("outlanded"):
            st.info(warning)


def _stat_tiles(metrics: FlightMetrics) -> None:
    """Headline numbers, one per metric family."""
    row1 = st.columns(4)
    row1[0].metric("Task speed", _fmt(metrics.task_speed_kmh, ".1f", " km/h"))
    row1[1].metric("Task distance", _fmt(metrics.task_distance_km, ".1f", " km"))
    row1[2].metric("Time on task", _duration(metrics.task_duration_s))
    row1[3].metric(
        "Start",
        f"{metrics.start_time:%H:%M}" if metrics.start_time else "—",
        _fmt(metrics.start_altitude, ".0f", " m"),
        delta_color="off",
    )

    row2 = st.columns(4)
    row2[0].metric(
        "Average climb",
        _fmt(metrics.average_climb_ms, ".2f", " m/s"),
        f"{metrics.thermal_count} thermals",
        delta_color="off",
    )
    row2[1].metric(
        "Circling",
        _fmt(metrics.percent_circling, ".0f", " %"),
        _duration(metrics.circling_s),
        delta_color="off",
    )
    row2[2].metric(
        "Cruise speed",
        _fmt(metrics.cruise_speed_kmh, ".0f", " km/h"),
        f"L/D {_fmt(metrics.glide_ratio, '.1f')}",
        delta_color="off",
    )
    row2[3].metric(
        "Cruise detour",
        _fmt(metrics.detour_percent, "+.1f", " %"),
        f"final glide {_fmt(metrics.final_glide_km, '.0f', ' km')}",
        delta_color="off",
    )


def _leg_table(metrics: FlightMetrics) -> None:
    """The table view. It is also the relief for chart colours that sit below the
    3:1 contrast floor on a light surface — every number is readable as text."""
    rows = [
        {
            "Leg": leg.label,
            "km": round(leg.task_distance_km, 1),
            "Time": _duration(leg.duration_s),
            "km/h": round(leg.speed_kmh, 1) if leg.speed_kmh else None,
            "Thermals": leg.thermal_count,
            "Climb m/s": round(leg.average_climb_ms, 2) if leg.average_climb_ms else None,
            "Circling %": round(leg.percent_circling, 1) if leg.percent_circling else None,
            "Cruise km/h": round(leg.cruise_speed_kmh, 1) if leg.cruise_speed_kmh else None,
            "L/D": round(leg.glide_ratio, 1) if leg.glide_ratio else None,
            "Detour %": round(leg.detour_percent, 1) if leg.detour_percent else None,
            "Band m": f"{leg.altitude_min:.0f}–{leg.altitude_max:.0f}",
        }
        for leg in metrics.legs
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)


def main() -> None:
    st.set_page_config(page_title="Debrief", page_icon="🛩", layout="wide")
    palette = palette_for(st.get_option("theme.base"))

    st.title("Debrief")

    path = _pick_flight()

    st.sidebar.header("Map")
    tile_source = st.sidebar.selectbox(
        "Base map", list(TILE_SOURCES), index=list(TILE_SOURCES).index(DEFAULT_TILE_SOURCE)
    )
    st.sidebar.caption(TILE_SOURCES[tile_source]["attribution"])

    if path is None:
        st.info(
            f"Drop IGC files into `{DEFAULT_ARCHIVE}/` (or upload one in the sidebar) "
            "to debrief a flight. Competition files from SoaringSpot carry their own "
            "task declaration, so nothing else is needed."
        )
        return

    try:
        metrics = _analyse_path(str(path), path.stat().st_mtime)
    except (IGCError, ValueError) as exc:
        st.error(str(exc))
        return

    _header(metrics)
    _stat_tiles(metrics)

    st.pydeck_chart(flight_deck(metrics, palette, tile_source), use_container_width=True)
    st.plotly_chart(barogram(metrics, palette), use_container_width=True)
    st.plotly_chart(climb_profile(metrics, palette), use_container_width=True)

    st.markdown("#### Legs")
    _leg_table(metrics)


main()
