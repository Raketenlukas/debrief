"""Plotly figures for a single analysed flight.

Two charts carry the debrief:

barogram
    altitude over time, with the trace split into cruise and climb. Splitting the
    line by phase rather than shading a band behind it means the thing a pilot
    actually looks for — where the climbs were and what they cost — is the
    encoding, not an overlay.
climb profile
    one bar per thermal, positioned at the time it happened and as wide as it
    lasted, so a long grind and a quick blip are visually different events.

Neither chart uses a second y-axis. Where two measures of different scale need
comparing, they get two charts.
"""

from __future__ import annotations

import datetime as dt

import plotly.graph_objects as go

from debrief.app.theme import Palette
from debrief.core.metrics import FlightMetrics, fix_altitude
from debrief.core.models import Fix

_FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'


def _base_layout(palette: Palette, title: str, height: int) -> dict:
    return dict(
        title=dict(text=title, font=dict(size=15, color=palette.ink_primary, family=_FONT)),
        height=height,
        margin=dict(l=56, r=20, t=64, b=58),
        paper_bgcolor=palette.surface,
        plot_bgcolor=palette.surface,
        font=dict(family=_FONT, size=12, color=palette.ink_secondary),
        hoverlabel=dict(font=dict(family=_FONT, size=12)),
        legend=dict(
            # Below the plot: turnpoint names are annotated along the top edge,
            # and a top-right legend lands on top of the last one.
            orientation="h",
            yanchor="top",
            y=-0.18,
            xanchor="left",
            x=0.0,
            bgcolor="rgba(0,0,0,0)",
            font=dict(color=palette.ink_secondary),
        ),
        xaxis=dict(
            gridcolor=palette.grid,
            linecolor=palette.axis,
            zeroline=False,
            tickfont=dict(color=palette.ink_muted),
            # Time only. Plotly otherwise adds a second row with the date, which
            # collides with the legend below the plot and repeats the date already
            # shown in the page header.
            tickformat="%H:%M",
        ),
        yaxis=dict(
            gridcolor=palette.grid,
            linecolor=palette.axis,
            zeroline=False,
            tickfont=dict(color=palette.ink_muted),
        ),
    )


def _phase_series(trace: list[Fix], spans: list[tuple[dt.datetime, dt.datetime]]) -> tuple[list, list]:
    """Build one x/y pair covering ``spans``, with a gap between each span.

    A single trace with ``None`` breaks keeps the legend to one entry per phase
    type instead of one per segment.
    """
    xs: list = []
    ys: list = []
    for start, end in spans:
        segment = [f for f in trace if start <= f["datetime"] <= end]
        if len(segment) < 2:
            continue
        xs.extend([f["datetime"] for f in segment] + [None])
        ys.extend([fix_altitude(f) for f in segment] + [None])
    return xs, ys


def barogram(metrics: FlightMetrics, palette: Palette, height: int = 380) -> go.Figure:
    """Altitude over task time, split into cruise and climb."""
    trace = metrics.flight.trace
    thermal_spans = [(t.start_time, t.end_time) for t in metrics.thermals]

    # Cruise is everything on task that is not a thermal, derived by complement so
    # the two series always tile the flight with no gaps or double-counting.
    cruise_spans: list[tuple[dt.datetime, dt.datetime]] = []
    cursor = metrics.start_time
    task_end = metrics.finish_time or (metrics.legs[-1].end_time if metrics.legs else None)
    if cursor and task_end:
        for start, end in sorted(thermal_spans):
            if start > cursor:
                cruise_spans.append((cursor, start))
            cursor = max(cursor, end)
        if cursor < task_end:
            cruise_spans.append((cursor, task_end))

    figure = go.Figure()
    for name, spans, color in (
        ("Cruise", cruise_spans, palette.cruise),
        ("Climb", thermal_spans, palette.thermal),
    ):
        xs, ys = _phase_series(trace, spans)
        figure.add_trace(
            go.Scatter(
                x=xs,
                y=ys,
                mode="lines",
                name=name,
                line=dict(color=color, width=2),
                connectgaps=False,
                hovertemplate="%{x|%H:%M:%S} · %{y:.0f} m<extra>" + name + "</extra>",
            )
        )

    # Leg boundaries, directly labelled with the turnpoint reached. Labels are
    # staggered onto a second row when two turnpoints fall close together in
    # time - a short run-in between the last turnpoint and the finish is normal,
    # and on one row those two labels overprint each other.
    task_seconds = sum(leg.duration_s for leg in metrics.legs) or 1.0
    min_gap = 0.06 * task_seconds
    previous_time = None
    row = 0
    for leg in metrics.legs:
        figure.add_vline(x=leg.end_time, line=dict(color=palette.axis, width=1, dash="dot"))
        name = (
            metrics.task.points[leg.index + 1].name if leg.index + 1 < len(metrics.task.points) else "finish"
        )
        close_to_previous = (
            previous_time is not None and (leg.end_time - previous_time).total_seconds() < min_gap
        )
        row = 1 - row if close_to_previous else 0
        previous_time = leg.end_time
        figure.add_annotation(
            x=leg.end_time,
            yref="paper",
            y=1.0 + 0.06 * row,
            text=name,
            showarrow=False,
            yanchor="bottom",
            font=dict(size=11, color=palette.ink_muted, family=_FONT),
        )

    layout = _base_layout(palette, "Barogram", height)
    layout["hovermode"] = "x unified"
    layout["yaxis"]["title"] = dict(text="Altitude (m)", font=dict(color=palette.ink_secondary))
    figure.update_layout(**layout)
    return figure


def climb_profile(metrics: FlightMetrics, palette: Palette, height: int = 300) -> go.Figure:
    """One bar per thermal: when it happened, how long it lasted, how well it went."""
    figure = go.Figure()

    for leg in metrics.legs:
        if not leg.thermals:
            continue
        color = palette.leg_color(leg.index)
        figure.add_trace(
            go.Bar(
                # Bars are centred on the middle of the climb and as wide as its
                # duration, so position and width both carry meaning.
                x=[t.start_time + (t.end_time - t.start_time) / 2 for t in leg.thermals],
                y=[t.average_climb_ms for t in leg.thermals],
                width=[t.duration_s * 1000 for t in leg.thermals],
                name=leg.label,
                marker=dict(color=color, line=dict(width=0)),
                customdata=[
                    (t.duration_s / 60.0, t.height_gain, t.entry_altitude, t.exit_altitude)
                    for t in leg.thermals
                ],
                hovertemplate=(
                    "%{x|%H:%M} · <b>%{y:.2f} m/s</b><br>"
                    "%{customdata[0]:.1f} min · +%{customdata[1]:.0f} m<br>"
                    "%{customdata[2]:.0f} m → %{customdata[3]:.0f} m"
                    "<extra>%{fullData.name}</extra>"
                ),
            )
        )

    if metrics.average_climb_ms is not None:
        figure.add_hline(
            y=metrics.average_climb_ms,
            line=dict(color=palette.ink_muted, width=1, dash="dash"),
            annotation_text=f"flight average {metrics.average_climb_ms:.2f} m/s",
            annotation_position="top right",
            annotation_font=dict(size=11, color=palette.ink_muted, family=_FONT),
        )

    layout = _base_layout(palette, "Climbs", height)
    layout["bargap"] = 0.0
    layout["yaxis"]["title"] = dict(text="Climb rate (m/s)", font=dict(color=palette.ink_secondary))
    figure.update_layout(**layout)
    return figure
