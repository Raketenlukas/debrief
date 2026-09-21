"""The map of what each stretch of course cost against a reference."""

from __future__ import annotations

import pydeck as pdk

from debrief.app.maps.base import TOOLTIP, hex_to_rgb, view_state
from debrief.app.maps.layers import airspace_layers, task_layers
from debrief.app.theme import Palette, basemap_style
from debrief.core.airspace import Airspace
from debrief.core.progress import (
    SEGMENT_M,
    DeltaSegment,
    PilotProgress,
    ProgressComparison,
    TaskRuler,
)

# Where a time difference starts being worth seeing, and where it is serious —
# per kilometre of course, so the thresholds follow the stretch length the
# reader chose. A racing glider covers a kilometre in roughly 35 s, so 1 s/km
# is about 3% off the pace and 4 s/km about 11%: the same judgement whether a
# stretch is two kilometres or twenty-five, which a fixed threshold would not
# be. (Fix them instead, and coarse stretches come out red from end to end
# while fine ones never leave the neutral band.)
#
# Proportional to the stretch, but not to the day: a scale that restretched
# itself to each day's spread would make every day look equally dramatic, and
# the point of a debrief is to tell a day you flew well from one you did not.
TIME_SOFT_S_PER_KM = 1.0
TIME_HARD_S_PER_KM = 4.0

# Height against the reference at the same point on task. Fifty metres is
# noise between two gliders; two hundred is a different decision about where to
# be, and usually the cause of which a lost minute is the symptom. Fixed, not
# per kilometre: height is a state you are in, not a rate you accumulate.
HEIGHT_SOFT_M = 50.0
HEIGHT_HARD_M = 200.0

PROGRESS_TIME = "time"
PROGRESS_HEIGHT = "height"


def progress_thresholds(mode: str, segment_m: float = SEGMENT_M) -> tuple[float, float, str]:
    """``(soft, hard, unit)`` for a progress mode at this stretch length."""
    if mode == PROGRESS_HEIGHT:
        return HEIGHT_SOFT_M, HEIGHT_HARD_M, " m"
    kilometres = segment_m / 1000.0
    return TIME_SOFT_S_PER_KM * kilometres, TIME_HARD_S_PER_KM * kilometres, " s"


def segment_loss(segment: DeltaSegment, mode: str) -> float | None:
    """The signed quantity this mode colours, with **positive meaning worse**.

    For height that means flipping the sign: being *below* the reference is the
    bad one, and a scale whose red arm meant "higher" would read backwards.
    """
    if mode == PROGRESS_HEIGHT:
        height = segment.height_delta_m
        return None if height is None else -height
    return segment.lost_s


def _progress_info(segment: DeltaSegment, reference_label: str, ruler: TaskRuler) -> str:
    lost = segment.lost_s
    height = segment.height_delta_m
    lines = [
        f"{segment.start.time:%H:%M:%S} → {segment.end.time:%H:%M:%S}",
        ruler.describe(segment.end.distance_m),
    ]
    if lost is None:
        lines.append(f"no comparison: {reference_label} never got this far")
    else:
        verb = "lost" if lost >= 0 else "gained"
        lines.append(
            f"{verb} {abs(lost):.0f} s over {segment.distance_km:.1f} km of task "
            f"({segment.seconds / 60.0:.0f} min of flying)"
        )
    if height is not None:
        side = "above" if height >= 0 else "below"
        lines.append(f"{abs(height):.0f} m {side} {reference_label} here")
    return "\n".join(lines)


def progress_deck(
    progress: ProgressComparison,
    subject: PilotProgress,
    palette: Palette,
    basemap: str,
    mode: str = PROGRESS_TIME,
    airspaces: list[Airspace] | None = None,
    highlights: int = 5,
    segment_m: float = SEGMENT_M,
) -> pdk.Deck:
    """One pilot's track, coloured by what each stretch of course cost.

    Deliberately one pilot rather than the whole selection. The diverging scale
    spends the colour channel on *polarity*, so it has none left for identity:
    two tracks both coloured by their gap would be indistinguishable from each
    other. The gap chart carries the whole field; this carries the geography.

    Width doubles as a magnitude channel. Under colour-blind simulation the red
    arm collapses toward the grey midpoint sooner than the blue one, so a badly
    lost stretch has to be visible as more than a hue.
    """
    soft, hard, _unit = progress_thresholds(mode, segment_m)
    reference_label = progress.reference_label

    layers: list[pdk.Layer] = []
    if airspaces:
        layers.extend(airspace_layers(airspaces, palette))
    if subject.metrics.task is not None:
        layers.extend(task_layers(subject.metrics.task, palette))

    # The reference's own track, so "where you were when you lost it" has
    # something to be relative to. Thin and muted: it is context, not a subject.
    # A field reference is a composite of many pilots and has no track to draw.
    if not progress.reference.synthetic and progress.reference.longitude:
        layers.append(
            pdk.Layer(
                "PathLayer",
                data=[
                    {
                        "path": [
                            [lon, lat]
                            for lon, lat in zip(
                                progress.reference.longitude, progress.reference.latitude, strict=True
                            )
                        ],
                        "info": f"{reference_label} (the reference)",
                    }
                ],
                get_path="path",
                get_color=hex_to_rgb(palette.ink_muted, 130),
                get_width=30,
                width_min_pixels=1,
                width_max_pixels=2,
                pickable=True,
            )
        )

    pieces = []
    for segment in subject.segments:
        loss = segment_loss(segment, mode)
        colour = palette.diverging.color(loss, soft, hard)
        magnitude = 0.0 if loss is None else min(abs(loss) / hard, 1.0)
        pieces.append(
            {
                "path": segment.path,
                "color": hex_to_rgb(colour, 235),
                "width": 45 + 110 * magnitude,
                "info": _progress_info(segment, reference_label, progress.ruler),
            }
        )

    if pieces:
        layers.append(
            pdk.Layer(
                "PathLayer",
                data=pieces,
                get_path="path",
                get_color="color",
                get_width="width",
                # Always the thickest line on the map. Where a stretch cost
                # nothing it is drawn in the same grey as the reference and the
                # task legs, and without a width floor the eye has three
                # identical hairlines and no way to tell which flight is which.
                width_min_pixels=3,
                width_max_pixels=10,
                pickable=True,
                auto_highlight=True,
            )
        )

    # The worst stretches, marked so they are found rather than hunted for. Ringed
    # in the chart surface colour: these sit on top of the track they belong to,
    # and without the ring the mark and the line merge into one blob.
    worst = [s for s in subject.ranked_segments(worst_first=True) if (segment_loss(s, mode) or 0) > soft]
    marks = []
    for rank, segment in enumerate(worst[:highlights], start=1):
        loss = segment_loss(segment, mode)
        marks.append(
            {
                "position": [segment.end.longitude, segment.end.latitude],
                "color": hex_to_rgb(palette.diverging.color(loss, soft, hard), 255),
                "radius": 260 - 22 * rank,
                "info": f"#{rank} worst stretch\n" + _progress_info(segment, reference_label, progress.ruler),
            }
        )
    if marks:
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                data=marks,
                get_position="position",
                get_fill_color="color",
                get_line_color=hex_to_rgb(palette.surface, 255),
                get_radius="radius",
                radius_min_pixels=5,
                radius_max_pixels=11,
                stroked=True,
                line_width_min_pixels=2,
                pickable=True,
            )
        )

    fixes = list(subject.metrics.flight.trace)
    view = view_state(fixes) if fixes else pdk.ViewState(latitude=0, longitude=0, zoom=1)
    return pdk.Deck(
        layers=layers,
        initial_view_state=view,
        map_provider="carto",
        map_style=basemap_style(basemap),
        tooltip=TOOLTIP,
    )
