"""Command-line debrief: ``debrief <file.igc>``.

Useful on its own, and it proves the analysis layer has no dependency on
Streamlit — the same call backs a web API later.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from debrief.core.igc import IGCError, load_igc
from debrief.core.metrics import FlightMetrics, analyse


def _duration(seconds: float | None) -> str:
    if not seconds:
        return "—"
    total = int(seconds)
    return f"{total // 3600}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def _num(value: float | None, spec: str = ".1f") -> str:
    return "—" if value is None else f"{value:{spec}}"


def render(metrics: FlightMetrics) -> str:
    flight = metrics.flight
    lines = [
        f"{flight.pilot.label}  ·  {flight.date:%Y-%m-%d}  ·  {flight.pilot.glider_model or '?'}",
        f"Task: {metrics.task.label}  ({metrics.task.task_type}, {metrics.task.n_legs} legs)",
        "",
        f"  {metrics.task_distance_km:.1f} km in {_duration(metrics.task_duration_s)}"
        f"  ->  {_num(metrics.task_speed_kmh)} km/h",
        f"  Climb    {_num(metrics.average_climb_ms, '.2f')} m/s over {metrics.thermal_count}"
        f" thermals, circling {_num(metrics.percent_circling, '.0f')}%",
        f"  Cruise   {_num(metrics.cruise_speed_kmh, '.0f')} km/h at L/D"
        f" {_num(metrics.glide_ratio)}, detour {_num(metrics.detour_percent, '+.1f')}%",
        f"  Final glide {_num(metrics.final_glide_km)} km for {_num(metrics.final_glide_height, '.0f')} m",
        "",
        f"  {'Leg':26s} {'km':>6s} {'time':>8s} {'km/h':>6s} {'th':>3s}"
        f" {'m/s':>5s} {'circ%':>6s} {'L/D':>5s} {'det%':>6s}",
    ]
    for leg in metrics.legs:
        lines.append(
            f"  {leg.label[:26]:26s} {leg.task_distance_km:6.1f} {_duration(leg.duration_s):>8s}"
            f" {_num(leg.speed_kmh):>6s} {leg.thermal_count:3d}"
            f" {_num(leg.average_climb_ms, '.2f'):>5s} {_num(leg.percent_circling, '.0f'):>6s}"
            f" {_num(leg.glide_ratio):>5s} {_num(leg.detour_percent, '+.1f'):>6s}"
        )
    if metrics.warnings:
        lines.extend(["", *(f"  ! {w}" for w in metrics.warnings)])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="debrief", description=__doc__)
    parser.add_argument("igc", type=Path, nargs="+", help="IGC file(s) to analyse")
    parser.add_argument(
        "--start-buffer",
        type=int,
        default=0,
        metavar="SECONDS",
        help="tolerance on the start gate, matching the scorer's leniency",
    )
    args = parser.parse_args(argv)

    failures = 0
    for index, path in enumerate(args.igc):
        if index:
            print()
        try:
            flight = load_igc(path, start_time_buffer=args.start_buffer)
            print(render(analyse(flight, start_time_buffer=args.start_buffer)))
        except (IGCError, ValueError) as exc:
            print(f"{path}: {exc}", file=sys.stderr)
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
