import datetime as dt

import pytest

from tests.fixtures.synthetic import DEFAULT_TASK, SHORT_FINAL_LEG_TASK, write_igc


@pytest.fixture(scope="session")
def synthetic_igc(tmp_path_factory):
    """A complete flown race task. Session-scoped: generating it takes ~0.1 s."""
    path = tmp_path_factory.mktemp("igc") / "7L.igc"
    write_igc(path)
    return path


@pytest.fixture(scope="session")
def second_pilot_igc(tmp_path_factory):
    """A different pilot flying the same task, a bit slower and weaker."""
    path = tmp_path_factory.mktemp("igc") / "XY.igc"
    write_igc(
        path,
        task=DEFAULT_TASK,
        date=dt.date(2024, 6, 15),
        pilot="Beat Muster",
        glider_model="ASG 29",
        glider_id="HB-3210",
        competition_id="XY",
        cruise_speed=29.0,
        climb_rate=1.8,
    )
    return path


@pytest.fixture(scope="session")
def short_final_leg_igc(tmp_path_factory):
    """A task whose last leg is a short run-in, flown without a climb.

    This is the ordinary shape of a real contest task, and the one that hid a
    final-glide bug: the last thermal sits on an earlier leg.
    """
    path = tmp_path_factory.mktemp("igc") / "SF.igc"
    write_igc(path, task=SHORT_FINAL_LEG_TASK)
    return path
