"""Local IGC archive: a directory of files you downloaded yourself."""

from __future__ import annotations

import logging
from collections.abc import Iterator
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
