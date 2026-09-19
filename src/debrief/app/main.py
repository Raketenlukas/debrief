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

from debrief.app import day_view  # noqa: E402
from debrief.app.charts import barogram, climb_profile  # noqa: E402
from debrief.app.maps import flight_deck  # noqa: E402
from debrief.app.theme import (  # noqa: E402
    BASEMAPS,
    DEFAULT_BASEMAP,
    airspace_family,
    palette_for,
)
from debrief.core.airspace import Airspace, load_openair  # noqa: E402
from debrief.core.igc import IGCError, load_igc  # noqa: E402
from debrief.core.metrics import FlightMetrics, analyse_or_summarise  # noqa: E402
from debrief.sources.local import LocalArchive  # noqa: E402
from debrief.sources.soaringspot import (  # noqa: E402
    SoaringSpotDay,
    SoaringSpotError,
    import_competition,
)

DEFAULT_ARCHIVE = Path("data/igc")
DEFAULT_AIRSPACE = Path("data/airspace")
METRES_TO_FEET = 3.280839895


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
    return analyse_or_summarise(load_igc(path))


def _safe_upload_path(directory: Path, name: str, fallback: str) -> Path:
    """Place an uploaded file inside ``directory``, never outside it.

    An upload's filename is attacker-controlled: browsers send only a basename,
    but nothing stops a crafted request to the upload endpoint from sending
    "../../../.ssh/authorized_keys". Joining that onto a directory and writing
    it escapes the directory, so the name is reduced to its final component and
    the result is checked against the directory before anything is written.
    """
    base = Path(name).name
    if not base or base in {".", ".."}:
        base = fallback
    candidate = (directory / base).resolve()
    if not candidate.is_relative_to(directory.resolve()):  # pragma: no cover - belt and braces
        raise ValueError(f"refusing to write an upload outside {directory}")
    return candidate


@st.cache_data(show_spinner=False)
def _load_airspace(path: str, mtime: float) -> list[Airspace]:
    """Cache on (path, mtime): a national OpenAIR file is thousands of records."""
    del mtime
    return load_openair(path)


def _airspace_controls(metrics: FlightMetrics | None, palette) -> list[Airspace]:
    """Pick an OpenAIR file and filter it down to what is worth drawing."""
    st.sidebar.header("Airspace")

    uploaded = st.sidebar.file_uploader("OpenAIR file", type=["txt", "air", "openair"])
    path: Path | None = None
    if uploaded is not None:
        target = Path(tempfile.gettempdir()) / "debrief-airspace"
        target.mkdir(exist_ok=True)
        path = _safe_upload_path(target, uploaded.name, "airspace.txt")
        path.write_bytes(uploaded.getvalue())
    else:
        candidates = sorted(DEFAULT_AIRSPACE.glob("*")) if DEFAULT_AIRSPACE.is_dir() else []
        candidates = [c for c in candidates if c.is_file() and not c.name.startswith(".")]
        if candidates:
            path = st.sidebar.selectbox("File", candidates, format_func=lambda p: p.name)

    if path is None:
        st.sidebar.caption(
            f"Drop an OpenAIR file into `{DEFAULT_AIRSPACE}/` to overlay airspace. "
            "openAIP publishes them per country, updated weekly."
        )
        return []

    try:
        airspaces = _load_airspace(str(path), path.stat().st_mtime)
    except (OSError, ValueError) as exc:
        st.sidebar.error(f"Could not read airspace: {exc}")
        return []

    if not airspaces:
        st.sidebar.warning("No airspace records found in that file.")
        return []

    families = sorted({airspace_family(a.airspace_class, a.airspace_type) for a in airspaces})
    shown = st.sidebar.multiselect("Show", families, default=families)
    airspaces = [a for a in airspaces if airspace_family(a.airspace_class, a.airspace_type) in shown]

    if metrics is not None:
        band_only = st.sidebar.checkbox(
            "Only airspace in my altitude band",
            value=True,
            help=(
                "Hides airspace the flight was never vertically near. Limits given "
                "above ground level are kept, since resolving them needs terrain data."
            ),
        )
        if band_only and metrics.legs:
            low = min(leg.altitude_min for leg in metrics.legs) * METRES_TO_FEET
            high = max(leg.altitude_max for leg in metrics.legs) * METRES_TO_FEET
            airspaces = [a for a in airspaces if a.intersects_band(low, high)]
            st.sidebar.caption(f"Flight band {low:.0f}-{high:.0f} ft")

    legend = " · ".join(f"<span style='color:{palette.airspace[f]}'>&#9632;</span> {f}" for f in shown)
    st.sidebar.markdown(
        f"{len(airspaces)} shown<br/>{legend}<br/><span style='opacity:.7'>Not for navigation.</span>",
        unsafe_allow_html=True,
    )
    return airspaces


def _soaringspot_import() -> None:
    """Download a SoaringSpot competition, or a single day of it.

    Behind a button rather than on every rerun: this fetches one file per
    competitor from someone else's server. Files already downloaded are
    skipped, so pressing it again is cheap and a part-finished import resumes.
    """
    with st.sidebar.expander("Import from SoaringSpot", expanded=False):
        # An st.rerun() discards anything written before it, so the outcome of
        # the previous run's import is carried across in session state and
        # shown here instead of vanishing.
        outcome = st.session_state.pop("soaringspot_outcome", None)
        if outcome:
            level, text = outcome
            (st.success if level == "ok" else st.warning)(text)

        url = st.text_input(
            "Any results URL from the competition",
            placeholder="https://www.soaringspot.com/en_gb/<comp>/results/<class>/<date>/daily",
            help=(
                "Paste any results link from the competition. The whole-competition "
                "import finds every class and day from it."
            ),
        )
        include_hc = st.checkbox("Include hors-concours pilots", value=True)

        whole = st.button("Download whole competition", disabled=not url, type="primary")
        single = st.button("Just this one day", disabled=not url)

        if not (whole or single):
            st.caption(
                "Every competitor's IGC file is downloaded. Each carries the task, "
                "so a day becomes comparable against a single task."
            )
            return

        progress = st.progress(0.0, text="Reading the results page…")

        try:
            if whole:

                def on_day(done: int, total: int, label: str) -> None:
                    progress.progress(min(done / max(total, 1), 1.0), text=f"{done}/{total} · {label}")

                days = import_competition(
                    url, DEFAULT_ARCHIVE, include_hc_competitors=include_hc, progress=on_day
                )
                flights = sum(len(list(day.archive_paths())) for day in days)
                message = f"Imported {flights} flights across {len(days)} days."
            else:

                def on_file(done: int, total: int) -> None:
                    progress.progress(min(done / max(total, 1), 1.0), text=f"Downloading {done}/{total}…")

                day = SoaringSpotDay(url, DEFAULT_ARCHIVE, include_hc_competitors=include_hc)
                if day.url_note:
                    st.caption(day.url_note)
                paths = day.download(progress=on_file)
                message = f"Imported {len(paths)} flights into `{day.day_directory}`."
                days = [day] if paths else []
        except SoaringSpotError as exc:
            progress.empty()
            st.error(str(exc))
            return

        progress.empty()
        st.session_state["soaringspot_outcome"] = (
            ("ok", message) if days else ("warn", "That page yielded no flights.")
        )
        # The day pickers were built earlier in this run, so they only see the
        # new files after a rerun.
        st.rerun()


def _pick_flight() -> Path | None:
    st.sidebar.header("Flight")

    uploaded = st.sidebar.file_uploader("Upload an IGC file", type=["igc"])
    if uploaded is not None:
        temp_dir = Path(tempfile.gettempdir()) / "debrief-uploads"
        temp_dir.mkdir(exist_ok=True)
        target = _safe_upload_path(temp_dir, uploaded.name, "upload.igc")
        target.write_bytes(uploaded.getvalue())
        return target

    _soaringspot_import()

    root = Path(st.sidebar.text_input("…or an archive directory", str(DEFAULT_ARCHIVE)))
    paths = list(LocalArchive(root).archive_paths())
    if not paths:
        st.sidebar.caption(f"No .igc files under `{root}`.")
        return None

    return st.sidebar.selectbox("Flight", paths, format_func=lambda p: str(p.relative_to(root)))


def _header(metrics: FlightMetrics) -> None:
    flight = metrics.flight
    st.subheader(flight.pilot.label)
    bits = [f"{flight.date:%d %B %Y}"]
    if metrics.task is not None:
        bits.append(metrics.task.label)
    else:
        bits.append("free flight — no declared task")
    if flight.pilot.glider_model:
        bits.insert(1, flight.pilot.glider_model)
    if flight.competition:
        bits.append(flight.competition.competition)
    st.caption(" · ".join(bits))

    if metrics.outlanded and metrics.task is not None:
        st.warning(f"Outlanding — {len(metrics.legs)} of {metrics.task.n_legs} legs flown.")
    if metrics.task is not None and metrics.task.geometry_assumed:
        st.info(
            "This task came from the file's C records, which carry no observation "
            "zones. Sector sizes are assumed, so leg times and speeds are approximate."
        )
    for warning in metrics.warnings:
        if not warning.startswith("outlanded"):
            st.info(warning)


def _stat_tiles(metrics: FlightMetrics) -> None:
    """Headline numbers, one per metric family."""
    row1 = st.columns(4)
    if metrics.has_task:
        row1[0].metric("Task speed", _fmt(metrics.task_speed_kmh, ".1f", " km/h"))
        row1[1].metric("Task distance", _fmt(metrics.task_distance_km, ".1f", " km"))
        row1[2].metric("Time on task", _duration(metrics.task_duration_s))
    else:
        # No task, so no task speed or task distance to report. Showing the
        # track length instead is honest; calling it "distance" would not be.
        row1[0].metric("Distance flown", _fmt(metrics.distance_flown_km, ".1f", " km"))
        band = f"{metrics.overall.altitude_min:.0f}–{metrics.overall.altitude_max:.0f} m"
        row1[1].metric("Altitude band", band)
        row1[2].metric("Airborne", _duration(metrics.task_duration_s))
    row1[3].metric(
        "Start" if metrics.has_task else "Takeoff",
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
    3:1 contrast floor on a light surface - every number is readable as text.

    Values are formatted as text so a genuinely absent measurement shows as an em
    dash. Left as floats, a missing value renders as the literal "None", which
    reads as a failure rather than as "this leg had no climb to measure" - and a
    leg flown without circling, the usual final run-in, has exactly that.
    """

    def num(value: float | None, digits: int) -> str:
        # `if value` would treat a legitimate 0.0 as missing; 0% circling on a
        # pure glide leg is a real measurement, not an absent one.
        return "\u2014" if value is None else f"{value:.{digits}f}"

    rows = [
        {
            "Leg": leg.label,
            "km": f"{leg.task_distance_km:.1f}",
            "Time": _duration(leg.duration_s),
            "km/h": num(leg.speed_kmh, 1),
            "Thermals": str(leg.thermal_count),
            "Climb m/s": num(leg.average_climb_ms, 2),
            "Circling %": num(leg.percent_circling, 1),
            "Cruise km/h": num(leg.cruise_speed_kmh, 1),
            "L/D": num(leg.glide_ratio, 1),
            "Detour %": num(leg.detour_percent, 1),
            "Band m": f"{leg.altitude_min:.0f}\u2013{leg.altitude_max:.0f}",
        }
        for leg in metrics.legs
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)


def main() -> None:
    st.set_page_config(page_title="Debrief", page_icon="🛩", layout="wide")
    palette = palette_for(st.get_option("theme.base"))

    st.title("Debrief")

    mode = st.sidebar.radio(
        "View",
        ["One flight", "Compare a day"],
        horizontal=True,
        help="Comparing needs a whole day in the archive; import one below.",
    )

    path = _pick_flight() if mode == "One flight" else None
    if mode == "Compare a day":
        # The importer lives in the flight picker, which the comparison view
        # does not draw; a day cannot be compared before it is downloaded.
        _soaringspot_import()

    st.sidebar.header("Map")
    basemap = st.sidebar.selectbox("Base map", list(BASEMAPS), index=list(BASEMAPS).index(DEFAULT_BASEMAP))
    st.sidebar.caption(BASEMAPS[basemap]["attribution"])

    if mode == "Compare a day":
        airspaces = _airspace_controls(None, palette)
        day_view.render(DEFAULT_ARCHIVE, palette, basemap, airspaces)
        return

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

    airspaces = _airspace_controls(metrics, palette)

    _header(metrics)
    _stat_tiles(metrics)

    st.pydeck_chart(flight_deck(metrics, palette, basemap, airspaces), use_container_width=True)
    st.caption("Drag to pan, scroll to zoom, hover the track or an airspace for detail.")
    st.plotly_chart(barogram(metrics, palette), use_container_width=True)
    st.plotly_chart(climb_profile(metrics, palette), use_container_width=True)

    if metrics.legs:
        st.markdown("#### Legs")
        _leg_table(metrics)


main()
