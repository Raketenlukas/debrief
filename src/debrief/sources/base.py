"""The importer seam.

Every way of getting flights into the archive implements :class:`FlightSource`.
Today there is one: a local directory of IGC files, filled by hand. The seam
exists now because the alternatives are already visible — a SoaringSpot daily
results page scraped for its IGC links, or the SoaringSpot API for competitions
where an organiser has issued a key — and both should slot in without the rest of
the application noticing.

The rule that keeps this honest: a source's job ends when the raw IGC file is on
disk with its provenance recorded. Parsing, task reconstruction and analysis are
the same code regardless of where the bytes came from.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path

from debrief.core.models import Flight


class FlightSource(ABC):
    """Produces :class:`Flight` objects from somewhere."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier, stored on each flight's provenance."""

    @abstractmethod
    def flights(self) -> Iterator[Flight]:
        """Yield every flight this source can currently provide."""

    def archive_paths(self) -> Iterator[Path]:
        """Raw files backing this source, if it keeps any."""
        return iter(())
