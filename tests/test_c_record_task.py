"""Task recovery from standard IGC C records.

The SoaringSpot `LCU::C`/`LSEEYOU OZ` comment lines are an addition; the
standard way to declare a task is C records. Reading only the comment lines
made an ordinary competition file report "no task", which is what this covers.
"""

import pytest

from debrief.core.igc import (
    ASSUMED_FINISH_RADIUS,
    ASSUMED_START_RADIUS,
    ASSUMED_TURNPOINT_RADIUS,
    load_igc,
    task_from_c_records,
)
from debrief.core.metrics import analyse
from tests.fixtures.synthetic import DEFAULT_TASK, TaskPointSpec, write_igc


@pytest.fixture(scope="module")
def c_record_igc(tmp_path_factory):
    path = tmp_path_factory.mktemp("igc") / "c-records.igc"
    write_igc(path, dialect="igc")
    return path


@pytest.fixture(scope="module")
def both_dialects_igc(tmp_path_factory):
    path = tmp_path_factory.mktemp("igc") / "both.igc"
    write_igc(path, dialect="both")
    return path


def test_c_records_alone_still_yield_a_task(c_record_igc):
    task = load_igc(c_record_igc).task
    assert task is not None, "a C-record declaration must not read as 'no task'"
    assert task.n_legs == len(DEFAULT_TASK) - 1
    assert [p.name for p in task.points] == [p.name for p in DEFAULT_TASK]


def test_c_record_task_is_marked_as_assumed(c_record_igc):
    """C records carry no sectors, so the zones are defaults — and saying so
    matters, because sector size changes leg times and therefore speeds."""
    task = load_igc(c_record_igc).task
    assert task.geometry_assumed is True
    assert task.points[0].r_max == ASSUMED_START_RADIUS
    assert task.points[1].r_max == ASSUMED_TURNPOINT_RADIUS
    assert task.points[-1].r_max == ASSUMED_FINISH_RADIUS


def test_declared_geometry_wins_when_both_are_present(both_dialects_igc):
    """Real competition files carry both; the declared sectors are the truth."""
    task = load_igc(both_dialects_igc).task
    assert task.geometry_assumed is False
    assert task.points[0].r_max == DEFAULT_TASK[0].radius


def test_a_c_record_task_can_be_analysed(c_record_igc):
    metrics = analyse(load_igc(c_record_igc))
    assert metrics.completed
    assert len(metrics.legs) == len(DEFAULT_TASK) - 1
    assert metrics.task_speed_kmh > 0


def test_takeoff_and_landing_placeholders_are_dropped():
    """0/0 entries bracket the task and are not turnpoints."""
    declaration = {
        "num_turnpoints": 1,
        "waypoints": [
            {"latitude": 0.0, "longitude": 0.0, "description": ""},
            {"latitude": 51.0, "longitude": 14.0, "description": "START"},
            {"latitude": 52.0, "longitude": 13.0, "description": "TP1"},
            {"latitude": 51.0, "longitude": 14.0, "description": "FINISH"},
            {"latitude": 0.0, "longitude": 0.0, "description": ""},
        ],
    }
    task = task_from_c_records(declaration)
    assert [p.name for p in task.points] == ["START", "TP1", "FINISH"]


def test_explicit_takeoff_and_landing_are_dropped_too():
    """Some loggers write real coordinates for takeoff and landing."""
    declaration = {
        "num_turnpoints": 1,
        "waypoints": [
            {"latitude": 50.9, "longitude": 13.9, "description": "TAKEOFF"},
            {"latitude": 51.0, "longitude": 14.0, "description": "START"},
            {"latitude": 52.0, "longitude": 13.0, "description": "TP1"},
            {"latitude": 51.0, "longitude": 14.0, "description": "FINISH"},
            {"latitude": 50.9, "longitude": 13.9, "description": "LANDING"},
        ],
    }
    task = task_from_c_records(declaration)
    assert [p.name for p in task.points] == ["START", "TP1", "FINISH"]


def test_inconsistent_declaration_is_refused_not_guessed():
    """Scoring the wrong task silently is worse than reporting none."""
    declaration = {
        "num_turnpoints": 5,  # claims 5, supplies 1
        "waypoints": [
            {"latitude": 51.0, "longitude": 14.0, "description": "START"},
            {"latitude": 52.0, "longitude": 13.0, "description": "TP1"},
            {"latitude": 51.0, "longitude": 14.0, "description": "FINISH"},
        ],
    }
    assert task_from_c_records(declaration) is None


@pytest.mark.parametrize(
    "declaration",
    [
        {},
        {"waypoints": []},
        {"waypoints": [{"latitude": 0.0, "longitude": 0.0, "description": ""}]},
        {"num_turnpoints": 0, "waypoints": [{"latitude": 51.0, "longitude": 14.0, "description": "A"}]},
    ],
)
def test_degenerate_declarations_yield_no_task(declaration):
    assert task_from_c_records(declaration) is None


def test_unnamed_turnpoints_get_a_placeholder_name():
    declaration = {
        "num_turnpoints": 1,
        "waypoints": [
            {"latitude": 51.0, "longitude": 14.0, "description": "  "},
            {"latitude": 52.0, "longitude": 13.0, "description": ""},
            {"latitude": 51.0, "longitude": 14.0, "description": "FINISH"},
        ],
    }
    task = task_from_c_records(declaration)
    assert [p.name for p in task.points] == ["TP0", "TP1", "FINISH"]


def test_outlanding_from_assumed_sectors_is_retried_not_believed(tmp_path):
    """A sector smaller than the declared one is never entered, so a completed
    task reads as an outlanding.

    Found on a real 401 km flight: its finish was a 5 km ring, the assumed
    default is 3 km, and the flight reported "outlanded on leg 5" despite
    having been scored as complete.
    """
    # Big sectors declared; the glider rounds them at 250 m, so the default
    # 500 m turnpoints are fine but the 3 km finish default is not.
    task = tuple(TaskPointSpec(p.name, p.lat, p.lon, radius=6000.0) for p in DEFAULT_TASK)
    path = tmp_path / "wide-sectors.igc"
    write_igc(path, task=task, dialect="igc")

    metrics = analyse(load_igc(path))

    assert metrics.completed, "widening should have recovered the completed task"
    assert not metrics.outlanded
    assert any("wider assumed sectors" in w for w in metrics.warnings)


def test_declared_geometry_is_never_widened(tmp_path):
    """With declared zones there is nothing to guess, so an outlanding stands."""
    task = tuple(TaskPointSpec(p.name, p.lat, p.lon, radius=6000.0) for p in DEFAULT_TASK)
    path = tmp_path / "declared.igc"
    write_igc(path, task=task, dialect="soaringspot")

    metrics = analyse(load_igc(path))
    assert not metrics.task.geometry_assumed
    assert not any("wider assumed sectors" in w for w in metrics.warnings)


def test_assumed_geometry_always_warns(c_record_igc):
    """Leg times depend on sector size, so scored numbers must say they are
    approximate even when nothing needed widening."""
    metrics = analyse(load_igc(c_record_igc))
    assert any("assumed, not declared" in w for w in metrics.warnings)
