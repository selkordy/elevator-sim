"""Summary statistics and plain-English observations for a run."""

from __future__ import annotations

import math
from typing import Dict, List, Sequence

from .simulation import SimResult


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile, ``q`` in [0, 100]."""
    if not values:
        return float("nan")
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    k = (len(xs) - 1) * q / 100
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def describe(values: Sequence[float]) -> Dict[str, float]:
    if not values:
        return {k: float("nan") for k in ("min", "max", "mean", "median", "p90", "p95", "p99", "std")}
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    return {
        "min": float(min(values)),
        "max": float(max(values)),
        "mean": mean,
        "median": percentile(values, 50),
        "p90": percentile(values, 90),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "std": math.sqrt(var),
    }


def summarize(result: SimResult) -> Dict:
    ps = result.passengers
    undelivered = [p.id for p in ps if p.dropoff_time is None]
    done = [p for p in ps if p.dropoff_time is not None]
    waits = [p.wait_time for p in done]
    travels = [p.travel_time for p in done]
    totals = [p.total_time for p in done]
    # Slowdown: total time relative to a private, instant, non-stop car.
    slowdowns = [p.total_time / abs(p.dest - p.source) for p in done]

    ticks = result.ticks
    cars = []
    for car in result.elevators:
        riders = sum(1 for p in done if p.elevator == car.id)
        cars.append(
            {
                "elevator": car.id,
                "riders": riders,
                "floors_travelled": car.floors_travelled,
                "stops": car.stops,
                "busy_share": car.busy_ticks / ticks if ticks else 0.0,
                "peak_load": max((row[car.id] for row in result.loads), default=0),
            }
        )

    return {
        "scheduler": result.scheduler,
        "passengers": len(ps),
        "delivered": len(done),
        "undelivered": undelivered,
        "skipped": list(result.skipped),
        "ticks": ticks,
        "wait": describe(waits),
        "travel": describe(travels),
        "total": describe(totals),
        "slowdown": describe(slowdowns),
        "zero_wait_share": (sum(1 for w in waits if w == 0) / len(waits)) if waits else float("nan"),
        "peak_waiting": max(result.waiting, default=0),
        "mean_waiting": (sum(result.waiting) / len(result.waiting)) if result.waiting else 0.0,
        "elevators": cars,
        "capacity": result.config.capacity,
        "starvation_threshold": result.config.starvation_threshold,
        "over_threshold": sum(1 for w in waits if w >= (result.config.starvation_threshold or math.inf)),
    }


def observations(summary: Dict) -> List[str]:
    """A few factual notes worth calling out, derived from the numbers."""
    notes: List[str] = []
    if not summary["delivered"]:
        return ["No passengers were delivered."]
    w, t, s = summary["wait"], summary["total"], summary["slowdown"]

    if summary["undelivered"]:
        notes.append(f"{len(summary['undelivered'])} passengers were never delivered.")
    else:
        notes.append(f"All {summary['delivered']} passengers were delivered in {summary['ticks']} ticks.")

    notes.append(
        f"Waiting is {w['mean'] / t['mean']:.0%} of the average total time "
        f"(mean wait {w['mean']:.1f} vs mean ride {summary['travel']['mean']:.1f})."
    )
    if t["median"] > 0:
        notes.append(
            f"Tail: p95 total time is {t['p95'] / t['median']:.1f}x the median "
            f"({t['p95']:.0f} vs {t['median']:.0f}); the slowest trip took {t['max']:.0f}."
        )
    notes.append(
        f"On average a trip takes {s['mean']:.2f}x as long as a private non-stop car "
        f"would (p95 {s['p95']:.2f}x)."
    )
    notes.append(f"{summary['zero_wait_share']:.0%} of passengers found their car already at the floor.")

    cars = summary["elevators"]
    if len(cars) > 1:
        riders = [c["riders"] for c in cars]
        busiest = max(cars, key=lambda c: c["riders"])
        idlest = min(cars, key=lambda c: c["riders"])
        if max(riders) > 1.5 * max(1, min(riders)):
            notes.append(
                f"Load is uneven: car {busiest['elevator']} carried {busiest['riders']} riders, "
                f"car {idlest['elevator']} only {idlest['riders']}."
            )
        util = sum(c["busy_share"] for c in cars) / len(cars)
        notes.append(f"Cars were busy {util:.0%} of the time on average.")
    full = [c["elevator"] for c in cars if c["peak_load"] >= summary["capacity"]]
    if full:
        notes.append(f"Cars {full} reached capacity at some point.")
    if summary["over_threshold"]:
        notes.append(
            f"{summary['over_threshold']} passengers waited past the starvation threshold "
            f"({summary['starvation_threshold']} ticks) and were given priority."
        )
    notes.append(
        f"At peak, {summary['peak_waiting']} passengers were waiting at once "
        f"(average {summary['mean_waiting']:.1f})."
    )
    return notes


def format_summary(summary: Dict) -> str:
    lines = [
        f"Scheduler: {summary['scheduler']}",
        f"Passengers: {summary['delivered']}/{summary['passengers']} delivered, "
        f"{summary['ticks']} ticks simulated",
        "",
        f"{'metric':<12}{'min':>8}{'mean':>9}{'median':>9}{'p95':>9}{'max':>8}",
    ]
    for key in ("wait", "travel", "total"):
        d = summary[key]
        lines.append(
            f"{key + ' time':<12}{d['min']:>8.0f}{d['mean']:>9.2f}{d['median']:>9.1f}"
            f"{d['p95']:>9.1f}{d['max']:>8.0f}"
        )
    lines.append("")
    lines.append(f"{'car':<5}{'riders':>8}{'floors':>9}{'stops':>8}{'busy':>8}{'peak load':>11}")
    for c in summary["elevators"]:
        lines.append(
            f"{c['elevator']:<5}{c['riders']:>8}{c['floors_travelled']:>9}{c['stops']:>8}"
            f"{c['busy_share']:>8.0%}{c['peak_load']:>11}"
        )
    lines.append("")
    lines.append("Observations:")
    lines.extend(f"  - {n}" for n in observations(summary))
    if summary["skipped"]:
        lines.append("")
        lines.append("Skipped input rows:")
        lines.extend(f"  - {s}" for s in summary["skipped"])
    return "\n".join(lines)
