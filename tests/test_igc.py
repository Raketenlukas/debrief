import datetime as dt

import pytest

from debrief.core.igc import IGCError, load_igc, to_opensoar_task
from debrief.core.models import TaskDef, TaskPoint


def test_loads_pilot_and_glider(synthetic_igc):
    flight = load_igc(synthetic_igc)
    assert flight.pilot.name == "Anna Beispiel"
    assert flight.pilot.competition_id == "7L"
    assert flight.pilot.glider_model == "Ventus 3"
    assert flight.pilot.glider_registration == "D-1234"
    assert flight.date == dt.date(2024, 6, 15)


def test_task_is_reconstructed_from_comment_lines(synthetic_igc):
    task = load_igc(synthetic_igc).task
    assert task is not None
    assert task.task_type == "race"
    assert task.n_legs == 3
    assert [p.name for p in task.points] == ["KLIX", "JUETERBOG", "RIESA", "KLIXFIN"]
    assert task.points[0].latitude == pytest.approx(51.2917, abs=1e-4)
    assert task.points[0].r_max == 5000


def test_same_task_flown_by_two_pilots_shares_a_task_key(synthetic_igc, second_pilot_igc):
    """The premise of same-task comparison: task identity must not depend on who flew it."""
    first = load_igc(synthetic_igc)
    second = load_igc(second_pilot_igc)
    assert first.task_key == second.task_key
    assert first.pilot_key != second.pilot_key
    assert first.flight_key != second.flight_key


def test_task_key_changes_when_geometry_changes(synthetic_igc):
    task = load_igc(synthetic_igc).task
    moved = TaskDef(
        points=(
            TaskPoint(**{**task.points[0].__dict__, "latitude": task.points[0].latitude + 0.01}),
            *task.points[1:],
        ),
        task_type=task.task_type,
    )
    assert moved.key != task.key


def test_task_round_trips_through_opensoar(synthetic_igc):
    task = load_igc(synthetic_igc).task
    rebuilt = to_opensoar_task(task)
    assert len(rebuilt.waypoints) == len(task.points)
    for original, waypoint in zip(task.points, rebuilt.waypoints, strict=True):
        assert waypoint.name == original.name
        assert waypoint.latitude == pytest.approx(original.latitude)
        assert waypoint.r_max == original.r_max
        assert waypoint.sector_orientation == original.sector_orientation


def test_missing_file_raises_igc_error(tmp_path):
    with pytest.raises(IGCError, match="no such file"):
        load_igc(tmp_path / "nope.igc")


def test_file_without_fixes_raises_igc_error(tmp_path):
    path = tmp_path / "empty.igc"
    path.write_text("AXXX001\nHFDTE150624\n")
    with pytest.raises(IGCError, match="no usable B records"):
        load_igc(path)
