"""The tables and the colour legends that sit under the maps.

Every one of these is a relief channel as well as a table: the palettes that
colour the maps stop being separable at large field sizes and under
colour-blindness, so the number has to be readable somewhere that is not the
picture.
"""

from __future__ import annotations

from debrief.app.format import clock, gap, number
from debrief.core.compare import DayComparison
from debrief.core.progress import PilotProgress


def comparison_table(day: DayComparison) -> list[dict]:
    """One row per pilot, covering all four comparison families.

    Ranks and percentiles are computed against this selection, not the whole
    field: comparing yourself with the three people you chose is the question
    being asked.
    """
    speed = day.distribution("task_speed_kmh", higher_is_better=True)
    climb = day.distribution("average_climb_ms", higher_is_better=True)
    glide = day.distribution("glide_ratio", higher_is_better=True)
    detour = day.distribution("detour_percent", higher_is_better=False)

    rows = []
    for comparison in day.comparisons():
        metrics = comparison.metrics
        rows.append(
            {
                "Pilot": metrics.flight.pilot.label,
                "km/h": number(metrics.task_speed_kmh),
                "Rank": speed.rank_of(metrics.task_speed_kmh) or "—",
                "Δ min:s": gap(comparison.total_delta_s),
                # Start tactics
                "Start": f"{metrics.start_time:%H:%M}" if metrics.start_time else "—",
                "Start m": number(metrics.start_altitude, 0),
                "→1st climb": gap(metrics.time_to_first_climb_s),
                # Climb quality
                "Climb m/s": number(metrics.average_climb_ms, 2),
                "Climb pct": number(climb.percentile_of(metrics.average_climb_ms), 0),
                "Best m/s": number(metrics.best_climb_ms, 2),
                "Circling %": number(metrics.percent_circling, 0),
                "Band m": number(metrics.climbing_altitude_mean, 0),
                # Cruise efficiency
                "Cruise km/h": number(metrics.cruise_speed_kmh, 0),
                "L/D": number(metrics.glide_ratio),
                "L/D pct": number(glide.percentile_of(metrics.glide_ratio), 0),
                "Detour %": number(metrics.detour_percent),
                "Detour pct": number(detour.percentile_of(metrics.detour_percent), 0),
            }
        )
    return rows


def swatch_legend(entries: list[tuple[str, str]]) -> str:
    """The colour scale as labelled swatches.

    A diverging scale is unreadable without one: the reader has to know which
    end is which, and no amount of intuition supplies the thresholds.
    """
    boxes = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:6px;margin-right:16px;">'
        f'<span style="width:22px;height:10px;border-radius:2px;background:{colour};'
        f'display:inline-block;"></span><span>{label}</span></span>'
        for colour, label in entries
    )
    return f'<div style="font-size:12px;opacity:0.85;margin:4px 0 10px;">{boxes}</div>'


def lost_table(pilot: PilotProgress, ruler, reference_label: str, count: int = 8) -> list[dict]:
    """The stretches of course that cost the most, in order, with when and where.

    The map shows where; this says exactly when, and puts a number on it. It is
    also the relief channel for the map's colours: the two near-neutral steps of
    a diverging scale are hard to separate under colour-blindness, so the
    finding must be readable without them.
    """
    rows = []
    for rank, segment in enumerate(pilot.ranked_segments(worst_first=True)[:count], start=1):
        height = segment.height_delta_m
        rows.append(
            {
                "#": rank,
                "From": f"{segment.start.time:%H:%M:%S}",
                "To": f"{segment.end.time:%H:%M:%S}",
                "Into task": clock(segment.start.elapsed_s),
                "Where": ruler.describe(segment.end.distance_m),
                "Lost s": f"{segment.lost_s:+.0f}",
                "Took": gap(segment.seconds),
                f"m vs {reference_label.split(' — ')[0]}": "—" if height is None else f"{height:+.0f}",
            }
        )
    return rows
