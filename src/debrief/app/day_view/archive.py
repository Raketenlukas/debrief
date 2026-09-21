"""Finding a competition day on disk and analysing it.

Analysis is the slow part of the whole application, so it is cached on the
files' paths and modification times: editing or re-importing a file busts the
entry, and switching back to a day you already looked at is free.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import streamlit as st

from debrief.core.igc import IGCError, load_igc
from debrief.core.metrics import FlightMetrics, analyse_or_summarise
from debrief.sources.local import ArchivedDay, archived_days


@st.cache_data(show_spinner=False)
def analyse_day(paths: tuple[str, ...], mtimes: tuple[float, ...]) -> list[FlightMetrics]:
    """Analyse a whole day. Cached on paths and mtimes, since this is the slow part."""
    del mtimes
    out = []
    for path in paths:
        try:
            out.append(analyse_or_summarise(load_igc(path)))
        except (IGCError, ValueError) as exc:
            st.warning(f"{Path(path).name}: {exc}")
    return out


def pick_day(archive_root: Path) -> ArchivedDay | None:
    days = archived_days(archive_root)
    if not days:
        st.info(
            "No competition days in the archive yet. Import one with "
            "**Import a SoaringSpot day** in the sidebar — it downloads every "
            "competitor's flight, which is what makes a comparison possible."
        )
        return None

    competitions = sorted({d.competition for d in days})
    competition = st.sidebar.selectbox("Competition", competitions)
    in_competition = [d for d in days if d.competition == competition]

    classes = sorted({d.plane_class for d in in_competition})
    plane_class = st.sidebar.selectbox("Class", classes)
    in_class = [d for d in in_competition if d.plane_class == plane_class]

    dates: list[dt.date] = sorted({d.date for d in in_class}, reverse=True)
    date = st.sidebar.selectbox("Day", dates, format_func=lambda d: f"{d:%a %d %b %Y}")

    for day in in_class:
        if day.date == date:
            return day
    return None
