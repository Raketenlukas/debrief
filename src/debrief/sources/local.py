"""Local IGC archive: a directory of files you downloaded yourself."""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from debrief.core.igc import IGCError, load_igc
from debrief.core.models import CompetitionDayRef, Flight
from debrief.sources.base import FlightSource

logger = logging.getLogger(__name__)

IGC_SUFFIXES = {".igc", ".IGC"}


class LocalArchive(FlightSource):
    """Every IGC file under a directory, recursively.

    Layout is free-form, but ``<competition>/<class>/<date>/<CN>.igc`` — the shape
    opensoar's SoaringSpot downloader writes — is understood: the directory names
    are picked up as competition metadata. Anything else still loads, just without
    that context.
    """

    def __init__(self, root: str | Path, strict: bool = False):
        self.root = Path(root)
        self.strict = strict

    @property
    def name(self) -> str:
        return "local"

    def archive_paths(self) -> Iterator[Path]:
        if not self.root.is_dir():
            return
        for path in sorted(self.root.rglob("*")):
            if path.suffix in IGC_SUFFIXES and path.is_file():
                yield path

    def _competition_for(self, path: Path) -> CompetitionDayRef | None:
        try:
            relative = path.relative_to(self.root)
        except ValueError:  # pragma: no cover - path always under root here
            return None
        parts = relative.parts[:-1]
        if len(parts) >= 2:
            return CompetitionDayRef(competition=parts[0], plane_class=parts[1])
        if len(parts) == 1:
            return CompetitionDayRef(competition=parts[0])
        return None

    def flights(self) -> Iterator[Flight]:
        for path in self.archive_paths():
            try:
                yield load_igc(path, competition=self._competition_for(path))
            except IGCError as exc:
                # One unreadable file in an archive of hundreds should not stop the
                # import; the caller can opt into strictness.
                if self.strict:
                    raise
                logger.warning("skipping %s: %s", path.name, exc)


@dataclass(frozen=True)
class ArchivedDay:
    """One competition day sitting in the archive."""

    competition: str
    plane_class: str
    date: dt.date
    directory: Path
    paths: tuple[Path, ...]

    @property
    def label(self) -> str:
        return f"{self.competition} · {self.plane_class} · {self.date:%Y-%m-%d} ({len(self.paths)})"


def archived_days(root: str | Path) -> list[ArchivedDay]:
    """Competition days already downloaded, newest first.

    Reads the layout the SoaringSpot importer writes,
    ``<competition>/<class>/<DD-MM-YYYY>/``. Directories that do not match are
    ignored rather than guessed at — a hand-organised archive is free to use any
    shape it likes, it simply will not appear as a day.
    """
    root = Path(root)
    if not root.is_dir():
        return []

    days: list[ArchivedDay] = []
    for date_dir in sorted(root.glob("*/*/*")):
        if not date_dir.is_dir():
            continue
        try:
            # A directory name is a calendar date, not a moment: the naive
            # datetime exists only to be thrown away by .date() on the same
            # line. Python has no date.strptime before 3.13.
            date = dt.datetime.strptime(date_dir.name, "%d-%m-%Y").date()  # noqa: DTZ007
        except ValueError:
            continue
        paths = tuple(sorted(p for p in date_dir.glob("*.igc") if p.is_file()))
        if not paths:
            continue
        days.append(
            ArchivedDay(
                competition=date_dir.parent.parent.name,
                plane_class=date_dir.parent.name,
                date=date,
                directory=date_dir,
                paths=paths,
            )
        )

    return sorted(days, key=lambda d: (d.date, d.competition, d.plane_class), reverse=True)
