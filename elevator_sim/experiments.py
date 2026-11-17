"""Experiments behind ``python -m elevator_sim compare``.

1. Scheduler comparison across four traffic patterns.
2. Load sweep: how each scheduler degrades as arrivals increase.
3. Fairness vs efficiency: destination dispatch with different cost exponents.
4. Express elevators: all-local fleet vs fleets with express cars, with and
   without door time.

Every number is averaged over several random traffic seeds. Each scenario
uses the same request files for every scheduler, so differences come from
the scheduler, not from the traffic.
"""

from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from . import io_utils
from .models import Request, SimConfig
from .schedulers import Scheduler, make_scheduler
from .simulation import simulate
from .stats import summarize
from .traffic import banked_layout, express_layout, generate

SCHEDULER_ORDER = ["destination_dispatch", "nearest_car", "zone", "round_robin"]
PATTERNS = ["up_peak", "down_peak", "lunch", "interfloor"]


def _metrics(summaries: List[Dict]) -> Dict:
    def avg(path):
        vals = []
        for s in summaries:
            v = s
            for k in path:
                v = v[k]
            vals.append(v)
        return sum(vals) / len(vals)

    return {
        "passengers": avg(["passengers"]),
        "undelivered": sum(len(s["undelivered"]) for s in summaries),
        "wait_mean": avg(["wait", "mean"]),
        "wait_p95": avg(["wait", "p95"]),
        "wait_max": max(s["wait"]["max"] for s in summaries),
        "total_mean": avg(["total", "mean"]),
        "total_median": avg(["total", "median"]),
        "total_p95": avg(["total", "p95"]),
        "total_p99": avg(["total", "p99"]),
        "total_max": max(s["total"]["max"] for s in summaries),
        "total_std": avg(["total", "std"]),
        "slowdown_mean": avg(["slowdown", "mean"]),
    }


def _run_many(config: SimConfig, make: Callable[[], Scheduler], traffic: Iterable[List[Request]]) -> Dict:
    return _metrics([summarize(simulate(config, make(), reqs)) for reqs in traffic])


def _traffic(pattern: str, floors: int, duration: int, rate: float, seeds: int) -> List[List[Request]]:
    return [
        generate(pattern, num_floors=floors, duration=duration, rate=rate, seed=1000 * s + 7)
        for s in range(seeds)
    ]


def _write_csv(rows: List[Dict], path: Path) -> None:
    if not rows:
        return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({k: (f"{v:.3f}" if isinstance(v, float) else v) for k, v in r.items()})


def _md_table(rows: List[Dict], cols: List[str], headers: Optional[List[str]] = None) -> str:
    headers = headers or cols
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for r in rows:
        cells = []
        for c in cols:
            v = r[c]
            cells.append(f"{v:.1f}" if isinstance(v, float) else str(v))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


# ---------------------------------------------------------------- experiments


def scheduler_comparison(seeds: int, quick: bool) -> List[Dict]:
    floors, duration, rate = 40, (300 if quick else 600), 0.5
    config = SimConfig(num_floors=floors, num_elevators=4, capacity=10)
    rows = []
    for pattern in PATTERNS:
        traffic = _traffic(pattern, floors, duration, rate, seeds)
        for name in SCHEDULER_ORDER:
            m = _run_many(config, lambda: make_scheduler(name), traffic)
            rows.append({"pattern": pattern, "scheduler": name, **m})
    return rows


def load_sweep(seeds: int, quick: bool) -> List[Dict]:
    floors, duration = 40, (300 if quick else 600)
    rates = [0.2, 0.6, 1.0] if quick else [0.2, 0.4, 0.6, 0.8, 1.0, 1.2]
    config = SimConfig(num_floors=floors, num_elevators=4, capacity=10)
    rows = []
    for pattern in ("up_peak", "lunch"):
        for rate in rates:
            traffic = _traffic(pattern, floors, duration, rate, seeds)
            for name in SCHEDULER_ORDER:
                m = _run_many(config, lambda: make_scheduler(name), traffic)
                rows.append({"pattern": pattern, "rate": rate, "scheduler": name, **m})
    return rows


FAIRNESS_SCENARIOS = [("up_peak", 0.8), ("lunch", 1.0), ("interfloor", 1.0)]


def fairness_sweep(seeds: int, quick: bool) -> List[Dict]:
    """Cost exponent sweep, with the simpler schedulers as reference points."""
    floors, duration = 40, (300 if quick else 600)
    config = SimConfig(num_floors=floors, num_elevators=4, capacity=10)
    exponents = [1.0, 2.0] if quick else [1.0, 1.5, 2.0, 3.0, 4.0]
    rows = []
    for pattern, rate in FAIRNESS_SCENARIOS:
        traffic = _traffic(pattern, floors, duration, rate, seeds)
        for p in exponents:
            m = _run_many(config, lambda: make_scheduler("destination_dispatch", fairness=p), traffic)
            rows.append({"pattern": pattern, "rate": rate, "label": f"DD p={p:g}", **m})
        for name, label in (("zone", "zone"), ("nearest_car", "nearest car")):
            m = _run_many(config, lambda: make_scheduler(name), traffic)
            rows.append({"pattern": pattern, "rate": rate, "label": label, **m})
    return rows


def starvation_sweep(seeds: int, quick: bool) -> List[Dict]:
    """How the anti-starvation threshold trades average time for a bounded wait."""
    floors, duration = 40, (300 if quick else 600)
    thresholds = [30, 120, 10**9] if quick else [30, 60, 120, 240, 10**9]
    rows = []
    for pattern, rate in FAIRNESS_SCENARIOS:
        traffic = _traffic(pattern, floors, duration, rate, seeds)
        for thr in thresholds:
            config = SimConfig(num_floors=floors, num_elevators=4, capacity=10, starvation_threshold=thr)
            m = _run_many(config, lambda: make_scheduler("destination_dispatch"), traffic)
            label = "off" if thr >= 10**9 else str(thr)
            rows.append({"pattern": pattern, "rate": rate, "threshold": label, **m})
    return rows


def express_comparison(seeds: int, quick: bool) -> List[Dict]:
    floors, cars, duration, rate = 60, 6, (300 if quick else 600), 0.6
    layouts = {
        "6 local": [None] * cars,
        "4 local + 2 express": express_layout(floors, cars, 2, express_from=31),
        "banked: 1 all + 2 low + 3 high": banked_layout(floors, low_cars=2, high_cars=3, split=31),
    }
    rows = []
    for stop_time in (0, 2):
        for pattern in ("up_peak", "lunch", "interfloor"):
            traffic = _traffic(pattern, floors, duration, rate, seeds)
            for label, served in layouts.items():
                config = SimConfig(
                    num_floors=floors,
                    num_elevators=cars,
                    capacity=12,
                    stop_time=stop_time,
                    served_floors=served,
                )
                m = _run_many(config, lambda: make_scheduler("destination_dispatch"), traffic)
                rows.append({"stop_time": stop_time, "pattern": pattern, "layout": label, **m})
    return rows


# ---------------------------------------------------------------------- main


def main(out: Path, seeds: int = 3, plots: bool = True, quick: bool = False) -> None:
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    if quick:
        seeds = min(seeds, 1)

    print("1/4 scheduler comparison ...", flush=True)
    sched = scheduler_comparison(seeds, quick)
    print("2/4 load sweep ...", flush=True)
    sweep = load_sweep(seeds, quick)
    print("3/4 fairness vs efficiency ...", flush=True)
    fair = fairness_sweep(seeds, quick)
    starve = starvation_sweep(seeds, quick)
    print("4/4 express elevators ...", flush=True)
    express = express_comparison(seeds, quick)

    _write_csv(sched, out / "scheduler_comparison.csv")
    _write_csv(sweep, out / "load_sweep.csv")
    _write_csv(fair, out / "fairness.csv")
    _write_csv(starve, out / "starvation_threshold.csv")
    _write_csv(express, out / "express.csv")

    # A sample run with its position log and charts, for the walkthrough.
    sample_dir = out / "sample_run"
    sample_dir.mkdir(exist_ok=True)
    sample_cfg = SimConfig(num_floors=40, num_elevators=4, capacity=10)
    sample_reqs = generate("lunch", num_floors=40, duration=300, rate=0.5, seed=42)
    io_utils.write_requests(sample_reqs, sample_dir / "requests.csv")
    sample = simulate(sample_cfg, make_scheduler("destination_dispatch"), sample_reqs)
    io_utils.write_positions(sample, sample_dir / "positions.csv")
    io_utils.write_passengers(sample, sample_dir / "passengers.csv")

    cols = ["scheduler", "wait_mean", "wait_p95", "total_mean", "total_p95", "total_max"]
    heads = ["scheduler", "mean wait", "p95 wait", "mean total", "p95 total", "max total"]
    md = [
        "# Experiment results",
        "",
        f"Generated by `python -m elevator_sim compare` ({seeds} traffic seed(s) per scenario"
        f"{', quick mode' if quick else ''}). All times are in ticks (1 tick = 1 floor of travel).",
        "",
        "## 1. Scheduler comparison",
        "",
        "40 floors, 4 cars of 10, no door time, 0.5 arrivals per tick for 600 ticks.",
        "",
    ]
    for pattern in PATTERNS:
        md += [f"**{pattern}**", "", _md_table([r for r in sched if r["pattern"] == pattern], cols, heads), ""]

    md += [
        "## 2. Load sweep (mean total time)",
        "",
        "Same building; arrivals per tick on the rows.",
        "",
    ]
    for pattern in ("up_peak", "lunch"):
        rates = sorted({r["rate"] for r in sweep if r["pattern"] == pattern})
        table = []
        for rate in rates:
            row = {"rate": f"{rate:g}"}
            for name in SCHEDULER_ORDER:
                r = next(x for x in sweep if x["pattern"] == pattern and x["rate"] == rate and x["scheduler"] == name)
                row[name] = r["total_mean"]
            table.append(row)
        md += [f"**{pattern}**", "", _md_table(table, ["rate"] + SCHEDULER_ORDER), ""]

    md += [
        "## 3. Fairness vs efficiency",
        "",
        "Destination dispatch minimising `sum(total_time ** p)`. p=1 is pure efficiency;",
        "larger p penalises long trips more (the default is p=2). Loads are near or past",
        "saturation, where the tail matters most.",
        "",
    ]
    fcols = ["label", "wait_mean", "total_mean", "total_p95", "total_p99", "total_max", "total_std"]
    fheads = ["scheduler", "mean wait", "mean total", "p95 total", "p99 total", "max total", "std"]
    for pattern, rate in FAIRNESS_SCENARIOS:
        sub = [r for r in fair if r["pattern"] == pattern]
        md += [f"**{pattern}, {rate:g} arrivals/tick**", "", _md_table(sub, fcols, fheads), ""]

    md += [
        "### Starvation threshold",
        "",
        "Destination dispatch (p=2). Once a car's oldest waiting rider has waited longer than",
        "the threshold, the car heads for them and only takes riders who will get off before",
        "reaching them, so there is guaranteed room. The default is 3 x floors = 120.",
        "",
    ]
    scols = ["threshold", "wait_mean", "total_mean", "total_p99", "wait_max", "total_max"]
    sheads = ["threshold", "mean wait", "mean total", "p99 total", "max wait", "max total"]
    for pattern, rate in FAIRNESS_SCENARIOS:
        sub = [r for r in starve if r["pattern"] == pattern]
        md += [f"**{pattern}, {rate:g} arrivals/tick**", "", _md_table(sub, scols, sheads), ""]

    md += [
        "## 4. Express and banked elevators",
        "",
        "60 floors, 6 cars of 12, destination dispatch, 0.6 arrivals per tick.",
        "*Express* cars serve the lobby and floors 31-60 and skip 2-30; the other four stop everywhere.",
        "*Banked*: two low-rise cars (1-30), three high-rise cars (lobby + 31-60, express through",
        "the low zone) and one all-floors car for trips between zones.",
        "",
    ]
    ecols = ["layout", "wait_mean", "total_mean", "total_p95", "total_max"]
    eheads = ["fleet", "mean wait", "mean total", "p95 total", "max total"]
    for stop_time in (0, 2):
        for pattern in ("up_peak", "lunch", "interfloor"):
            sub = [r for r in express if r["pattern"] == pattern and r["stop_time"] == stop_time]
            md += [f"**{pattern}, stop time {stop_time}**", "", _md_table(sub, ecols, eheads), ""]

    undelivered = sum(r["undelivered"] for r in sched + sweep + fair + starve + express)
    md += [
        "## Sanity",
        "",
        f"Undelivered passengers across every run: {undelivered}.",
        "",
    ]

    if plots:
        from . import plots as P

        charts = [
            P.plot_scheduler_comparison(sched, out / "scheduler_comparison.png"),
            P.plot_load_sweep(sweep, out / "load_sweep.png"),
            P.plot_fairness(fair, out / "fairness.png"),
            P.plot_express([r for r in express if r["stop_time"] == 0], out / "express.png"),
        ]
        charts += P.plot_run(sample, sample_dir)
        md += ["## Charts", ""]
        md += [f"![{c.stem}]({c.relative_to(out).as_posix()})" for c in charts]
        md += [""]

    (out / "results.md").write_text("\n".join(md))
    print("\n".join(md))
    print(f"\nWrote results to {out}/ in {time.time() - started:.1f}s")
