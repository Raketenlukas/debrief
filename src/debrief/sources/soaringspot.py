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
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

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


@dataclass(frozen=True)
class DayLink:
    """One class-day of a competition, as advertised by its results pages."""

    url: str
    competition: str
    plane_class: str
    date: dt.date

    @property
    def label(self) -> str:
        return f"{self.plane_class} · {self.date:%Y-%m-%d}"


def competition_results_url(url: str) -> str:
    """The competition's results index, from any URL inside that competition.

    Pasting one link should be enough, so the index is derived rather than asked
    for: everything up to and including ``/results``.
    """
    parsed = urlparse(url if "://" in url else f"https://{url}")
    parts = [p for p in parsed.path.split("/") if p]
    if "results" in parts:
        parts = parts[: parts.index("results") + 1]
    elif len(parts) >= 2:
        parts = [*parts[:2], "results"]
    else:
        raise SoaringSpotError(f"cannot find a competition in {url!r}")
    return parsed._replace(path="/" + "/".join(parts), query="", fragment="").geturl()


def discover_days(url: str, session=None) -> list[DayLink]:
    """Every class and day a competition publishes, found from one link.

    Discovery reads anchors, not tables. Results *tables* vary by competition
    and change between seasons; a link to a day is a link whatever markup wraps
    it, so this is the sturdier half of the scraping.

    The index is read first, then each class page it reveals, because some
    competitions list only the classes up front and the days one level down.
    """
    import requests
    from bs4 import BeautifulSoup

    session = session or requests
    index_url = competition_results_url(url)
    competition = parse_daily_url(url)[0] if "results" in url else None

    def links_on(page_url: str) -> set[DayLink]:
        try:
            response = session.get(page_url, timeout=30)
            response.raise_for_status()
        except Exception as exc:
            raise SoaringSpotError(f"could not read {page_url}: {exc}") from exc

        found: set[DayLink] = set()
        for anchor in BeautifulSoup(response.text, "html.parser").find_all("a", href=True):
            absolute = urljoin(page_url, anchor["href"])
            try:
                comp, plane_class, date = parse_daily_url(absolute)
            except SoaringSpotError:
                continue  # not a day link; most of a page is not
            if competition and comp != competition:
                continue
            found.add(
                DayLink(
                    url=normalise_daily_url(absolute)[0],
                    competition=comp,
                    plane_class=plane_class,
                    date=date,
                )
            )
        return found

    days = links_on(index_url)
    for plane_class in sorted({day.plane_class for day in days}):
        days |= links_on(f"{index_url}/{plane_class}")

    return sorted(days, key=lambda d: (d.date, d.plane_class))


def import_days(
    url: str,
    archive_root: str | Path,
    plane_class: str | None = None,
    include_hc_competitors: bool = True,
    progress=None,
) -> list[SoaringSpotDay]:
    """Download the days a competition publishes, optionally one class only.

    Days that fail are reported and skipped: one unreadable day should not cost
    the rest of a two-week contest.
    """
    links = discover_days(url)
    if plane_class is not None:
        links = [link for link in links if link.plane_class == plane_class]
        if not links:
            raise SoaringSpotError(
                f"no days found for class {plane_class!r}. The competition publishes "
                "other classes; check the class in the URL."
            )
    if not links:
        raise SoaringSpotError(
            f"no competition days found from {url!r}. The results page may use a "
            "layout this cannot read; importing a single day still works."
        )

    imported: list[SoaringSpotDay] = []
    for index, link in enumerate(links, start=1):
        if progress is not None:
            progress(index - 1, len(links), link.label)
        day = SoaringSpotDay(link.url, archive_root, include_hc_competitors=include_hc_competitors)
        try:
            day.download()
        except SoaringSpotError as exc:
            logger.warning("skipping %s: %s", link.label, exc)
            continue
        imported.append(day)

    if progress is not None:
        progress(len(links), len(links), "done")
    return imported


def import_class(
    url: str,
    archive_root: str | Path,
    include_hc_competitors: bool = True,
    progress=None,
) -> list[SoaringSpotDay]:
    """Every day of the class the URL names.

    The usual unit of interest: a pilot flies one class, and the other classes
    are a download nobody asked for. The class is taken from the URL rather
    than chosen separately, so pasting any of that class's results links is
    enough.
    """
    _, plane_class, _ = parse_daily_url(url)
    return import_days(
        url,
        archive_root,
        plane_class=plane_class,
        include_hc_competitors=include_hc_competitors,
        progress=progress,
    )


def import_competition(
    url: str,
    archive_root: str | Path,
    include_hc_competitors: bool = True,
    progress=None,
) -> list[SoaringSpotDay]:
    """Every class and every day. Rarely what you want; see :func:`import_class`."""
    return import_days(
        url,
        archive_root,
        plane_class=None,
        include_hc_competitors=include_hc_competitors,
        progress=progress,
    )
