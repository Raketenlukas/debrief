"""Import a SoaringSpot competition day.

Scraping is delegated to ``opensoar.competition.soaringspot.SoaringSpotDaily``,
which already knows the awkward part: each competitor's IGC link is not a plain
``href`` but lives inside the ``data-content`` popover attribute on the contest
-number column. There is no authentication.

This module only orchestrates. Once the raw files are on disk they are parsed by
:func:`debrief.core.igc.load_igc` like any other file, so a downloaded flight and
a hand-copied one go through exactly the same code — which is the point of the
importer seam.

The URL is a *daily results page*, of the shape::

    https://www.soaringspot.com/en/<competition>/results/<class>/<date>/daily

Be a polite client: the files are the durable asset, so fetch a day once and keep
it. Re-running is cheap because opensoar skips files already downloaded.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse

from debrief.core.igc import IGCError, load_igc
from debrief.core.models import CompetitionDayRef, Flight, FlightSource
from debrief.sources.base import FlightSource as FlightSourceBase

logger = logging.getLogger(__name__)


class SoaringSpotError(RuntimeError):
    """Raised when a day cannot be imported."""


# SoaringSpot shows a day's results under tabs: "daily" has each competitor's
# flight, "total" has the cumulative standings. Only the daily page carries the
# per-flight IGC links, so a total URL is switched over rather than failing.
RESULTS_TABS = {"daily", "total"}


def normalise_daily_url(url: str) -> tuple[str, str | None]:
    """Point a results URL at the daily tab. Returns (url, note-if-changed).

    Copying the URL from the browser usually lands on whichever tab was open,
    and "total" is a common one to be reading. Silently scraping the wrong page
    would just find no flights.
    """
    parsed = urlparse(url if "://" in url else f"https://{url}")
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 5 or parts[2] != "results":
        return url, None

    if len(parts) == 5:
        parts.append("daily")
        note = "no results tab in the URL; using the daily page"
    elif parts[5] != "daily":
        was = parts[5]
        parts[5] = "daily"
        note = (
            f"switched from the {was!r} tab to 'daily': only the daily page "
            "carries each competitor's IGC link"
        )
    else:
        return url, None

    rebuilt = parsed._replace(path="/" + "/".join(parts)).geturl()
    return rebuilt, note


def parse_daily_url(url: str) -> tuple[str, str, dt.date]:
    """Pull competition, class and date out of a daily results URL.

    opensoar derives the same three by positional split, so a URL it cannot read
    fails deep inside the download with an unhelpful error. Checking here turns
    that into something a user can act on.
    """
    parsed = urlparse(url if "://" in url else f"https://{url}")
    parts = [p for p in parsed.path.split("/") if p]
    # <lang>/<competition>/results/<class>/<date-description>/...
    if len(parts) < 5 or parts[2] != "results":
        raise SoaringSpotError(
            f"{url!r} is not a SoaringSpot daily results URL. Expected the shape "
            "https://www.soaringspot.com/en/<competition>/results/<class>/<date>/daily"
        )
    competition, _, plane_class, date_description = parts[1], parts[2], parts[3], parts[4]

    # The date is the trailing YYYY-MM-DD of the date segment.
    tail = date_description[-10:]
    try:
        date = dt.date.fromisoformat(tail)
    except ValueError as exc:
        raise SoaringSpotError(
            f"could not read a date from {date_description!r}; expected it to end in YYYY-MM-DD"
        ) from exc
    return competition, plane_class, date


class SoaringSpotDay(FlightSourceBase):
    """Every competitor's flight from one SoaringSpot daily results page."""

    def __init__(
        self,
        url: str,
        archive_root: str | Path,
        include_hc_competitors: bool = True,
    ):
        self.url, self.url_note = normalise_daily_url(url)
        self.archive_root = Path(archive_root)
        self.include_hc_competitors = include_hc_competitors
        self.competition, self.plane_class, self.date = parse_daily_url(self.url)

    @property
    def name(self) -> str:
        return "soaringspot"

    @property
    def day_directory(self) -> Path:
        """Where opensoar puts the files: <root>/<comp>/<class>/<DD-MM-YYYY>/."""
        return self.archive_root / self.competition / self.plane_class / self.date.strftime("%d-%m-%Y")

    def download(self, progress=None) -> list[Path]:
        """Fetch every competitor's IGC file. Returns the files on disk.

        :param progress: optional ``func(done, total)`` for a progress bar.
        """
        # Imported here so the rest of the app does not pay for opensoar's
        # scraping dependencies (requests, bs4) until a download is asked for.
        from opensoar.competition.soaringspot import SoaringSpotDaily

        try:
            SoaringSpotDaily(self.url).generate_competition_day(
                target_directory=str(self.archive_root),
                download_progress=progress,
                include_hc_competitors=self.include_hc_competitors,
            )
        except Exception as exc:  # network, markup change, empty results page
            raise SoaringSpotError(f"could not import {self.url}: {exc}") from exc

        # A list, not the iterator archive_paths() yields: callers count these.
        return list(self.archive_paths())

    def archive_paths(self) -> Iterator[Path]:
        directory = self.day_directory
        if not directory.is_dir():
            return iter(())
        return iter(sorted(p for p in directory.glob("*.igc") if p.is_file()))

    def flights(self) -> Iterator[Flight]:
        """Load the downloaded files. Download first if nothing is there yet."""
        paths = list(self.archive_paths())
        if not paths:
            paths = list(self.download())

        competition = CompetitionDayRef(
            competition=self.competition,
            plane_class=self.plane_class,
            date=self.date,
        )
        for path in paths:
            try:
                flight = load_igc(path, competition=competition)
            except IGCError as exc:
                # One unreadable file should not cost the rest of the day.
                logger.warning("skipping %s: %s", path.name, exc)
                continue
            # Record where it really came from; load_igc only sees a local file.
            flight.source = FlightSource(
                kind=self.name,
                reference=self.url,
                imported_at=dt.datetime.now(dt.UTC),
            )
            yield flight
