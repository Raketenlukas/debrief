"""The gap chart.

The map carries one pilot's geography; this chart is where the whole selection
is compared at once, so the thing to guard is that it stays a gap chart: one
axis, distance along the course, and no line for the pilot it is measured
against.
"""

import pytest

from debrief.app.charts import progress_delta_chart
from debrief.app.theme import LIGHT
from debrief.core.igc import load_igc
from debrief.core.metrics import analyse_or_summarise
from debrief.core.progress import FIELD_BEST, ProgressComparison


@pytest.fixture(scope="module")
def progress(synthetic_igc, second_pilot_igc):
    flights = [analyse_or_summarise(load_igc(p)) for p in (synthetic_igc, second_pilot_igc)]
    return ProgressComparison.build(flights, reference=flights[0])


def test_the_reference_gets_no_line_of_its_own(progress):
    """A pilot against themselves is a flat line at zero, which says nothing
    and takes a palette slot to say it."""
    figure = progress_delta_chart(progress, LIGHT)
    names = [trace.name for trace in figure.data]
    reference = next(p for p in progress.pilots if p.is_reference)
    assert reference.label not in names
    assert names == [p.label for p in progress.pilots if not p.is_reference]


def test_the_x_axis_is_course_not_time(progress):
    """At equal times two pilots are in different places, which is exactly why
    comparing them at equal times says nothing."""
    figure = progress_delta_chart(progress, LIGHT)
    assert "km" in figure.layout.xaxis.title.text
    ruler = progress.ruler
    for trace in figure.data:
        assert max(trace.x) <= ruler.total_m / 1000.0 * 1.05
        assert all(b >= a for a, b in zip(trace.x, trace.x[1:], strict=False))


def test_the_chart_never_grows_a_second_y_axis(progress):
    figure = progress_delta_chart(progress, LIGHT)
    assert not any(key.startswith("yaxis2") for key in figure.layout.to_plotly_json())
    assert all(trace.yaxis in (None, "y") for trace in figure.data)


def test_turnpoints_are_marked_along_the_course(progress):
    """'62 km' means nothing to a pilot; 'just past the second turnpoint' does."""
    figure = progress_delta_chart(progress, LIGHT)
    annotations = [a.text for a in figure.layout.annotations]
    inner = [p.name for p in progress.ruler.task.points[1:-1]]
    assert annotations == inner


def test_colours_follow_the_pilot_not_the_rank(progress):
    """Deselecting a pilot must not repaint the survivors."""
    figure = progress_delta_chart(progress, LIGHT)
    colours = [trace.line.color for trace in figure.data]
    assert len(set(colours)) == len(colours)
    assert set(colours) <= set(LIGHT.series)


def test_a_field_reference_is_named_in_the_title(synthetic_igc, second_pilot_igc):
    flights = [analyse_or_summarise(load_igc(p)) for p in (synthetic_igc, second_pilot_igc)]
    progress = ProgressComparison.build(flights, reference=FIELD_BEST)
    figure = progress_delta_chart(progress, LIGHT)
    assert "best" in figure.layout.title.text
    # Nobody is the reference, so every pilot gets a line.
    assert len(figure.data) == len(progress.pilots)
