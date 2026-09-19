"""An uploaded filename is attacker-controlled input.

Browsers send only a basename, but the upload endpoint is reachable directly,
so the app must not trust the name it is given. These tests pin the containment
property rather than any particular sanitising trick.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from debrief.app.main import _safe_upload_path  # noqa: E402


@pytest.mark.parametrize(
    "name",
    [
        "../../../../etc/passwd",
        "../escape.igc",
        "/etc/shadow",
        "..\\..\\windows\\system32\\evil.igc",
        "sub/dir/flight.igc",
        "....//....//escape.igc",
    ],
)
def test_hostile_names_stay_inside_the_directory(tmp_path, name):
    resolved = _safe_upload_path(tmp_path, name, "fallback.igc")
    assert resolved.is_relative_to(tmp_path.resolve())
    assert resolved.parent == tmp_path.resolve()


def test_ordinary_name_is_preserved(tmp_path):
    assert _safe_upload_path(tmp_path, "7L-klix.igc", "fallback.igc").name == "7L-klix.igc"


@pytest.mark.parametrize("name", ["", ".", ".."])
def test_degenerate_names_fall_back(tmp_path, name):
    """A name that reduces to nothing must not resolve to the directory itself."""
    resolved = _safe_upload_path(tmp_path, name, "fallback.igc")
    assert resolved.name == "fallback.igc"
    assert resolved != tmp_path.resolve()


def test_traversal_actually_writes_inside(tmp_path):
    """The property that matters: the bytes land where we intended."""
    outside = tmp_path.parent / "ESCAPED.igc"
    inbox = tmp_path / "inbox"
    inbox.mkdir()

    target = _safe_upload_path(inbox, "../ESCAPED.igc", "fallback.igc")
    target.write_bytes(b"payload")

    assert not outside.exists(), "upload escaped the directory"
    assert (inbox / "ESCAPED.igc").read_bytes() == b"payload"
