"""Command-line interface: ``python -m elevator_sim {run,generate,compare}``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from . import io_utils
from .models import SimConfig
from .schedulers import SCHEDULERS, make_scheduler
from .simulation import simulate
from .stats import format_summary, summarize
from .traffic import PATTERNS, express_layout, generate, parse_floor_spec


def _add_building_args(p: argparse.ArgumentParser, floors_default: Optional[int]) -> None:
    g = p.add_argument_group("building")
    g.add_argument(
        "--floors",
        type=int,
        default=floors_default,
        help="number of floors (default: inferred from the highest floor in the input)"
        if floors_default is None
        else f"number of floors (default: {floors_default})",
    )
    g.add_argument("--lowest-floor", type=int, default=1, help="number of the lobby floor (default: 1)")
    g.add_argument("--elevators", type=int, default=4, help="number of cars (default: 4)")
    g.add_argument("--capacity", type=int, default=10, help="max riders per car (default: 10)")
    g.add_argument(
        "--stop-time",
        type=int,
        default=0,
        help="extra ticks a car spends at a floor when it stops (default: 0, as in the spec)",
    )
    g.add_argument(
        "--starvation-threshold",
        type=int,
        default=None,
        help="ticks of waiting after which a rider gets priority (default: 3 x floors)",
    )
    g.add_argument(
        "--express",
        type=int,
        default=0,
        metavar="N",
        help="make the last N cars express: lobby plus floors from --express-from up",
    )
    g.add_argument("--express-from", type=int, default=None, help="lowest floor express cars serve above the lobby")
    g.add_argument(
        "--serve",
        action="append",
        default=[],
        metavar="CAR:FLOORS",
        help='restrict a car to some floors, e.g. --serve "3:1,30-60" (repeatable)',
    )


def _add_scheduler_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("scheduler")
    g.add_argument(
        "--scheduler",
        choices=sorted(SCHEDULERS),
        default="destination_dispatch",
        help="dispatch algorithm (default: destination_dispatch)",
    )
    g.add_argument(
        "--fairness",
        type=float,
        default=2.0,
        help="destination_dispatch cost exponent: 1 = pure efficiency, higher favours "
        "the slowest riders (default: 2)",
    )
    g.add_argument(
        "--busy-weight",
        type=float,
        default=0.0,
        help="destination_dispatch: extra cost per tick a car stays busy (default: 0)",
    )
    g.add_argument("--zones", type=int, default=None, help="zone scheduler: number of floor bands")


def build_config(args: argparse.Namespace, num_floors: int) -> SimConfig:
    served: List = [None] * args.elevators
    if args.express:
        served = express_layout(num_floors, args.elevators, args.express, args.express_from, args.lowest_floor)
    for spec in args.serve:
        try:
            car, floors = spec.split(":", 1)
            served[int(car)] = parse_floor_spec(floors)
        except (ValueError, IndexError):
            raise SystemExit(f"bad --serve value {spec!r}; expected CAR:FLOORS like 3:1,30-60")
    return SimConfig(
        num_floors=num_floors,
        num_elevators=args.elevators,
        capacity=args.capacity,
        lowest_floor=args.lowest_floor,
        stop_time=args.stop_time,
        starvation_threshold=args.starvation_threshold,
        served_floors=served,
    )


def cmd_run(args: argparse.Namespace) -> int:
    requests = io_utils.read_requests(sys.stdin if args.input == "-" else args.input)
    floors = args.floors
    if floors is None:
        top = max([r.source for r in requests] + [r.dest for r in requests], default=args.lowest_floor + 1)
        floors = max(2, top - args.lowest_floor + 1)
    config = build_config(args, floors)
    scheduler = make_scheduler(
        args.scheduler, fairness=args.fairness, busy_weight=args.busy_weight, zones=args.zones
    )
    result = simulate(config, scheduler, requests)
    summary = summarize(result)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    io_utils.write_positions(result, out / "positions.csv")
    io_utils.write_passengers(result, out / "passengers.csv")
    (out / "summary.json").write_text(json.dumps(summary, indent=2))

    print(
        f"Building: floors {config.lowest_floor}-{config.highest_floor}, "
        f"{config.num_elevators} cars x {config.capacity} riders, stop time {config.stop_time}"
    )
    print(format_summary(summary))
    print(f"\nWrote {out / 'positions.csv'}, {out / 'passengers.csv'}, {out / 'summary.json'}")

    if args.plots:
        from . import plots

        written = plots.plot_run(result, out)
        for path in written:
            print(f"Wrote {path}")
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    requests = generate(
        args.pattern,
        num_floors=args.floors,
        duration=args.duration,
        rate=args.rate,
        lowest_floor=args.lowest_floor,
        seed=args.seed,
    )
    if args.output == "-":
        import csv

        w = csv.writer(sys.stdout)
        w.writerow(io_utils.REQUIRED)
        for r in requests:
            w.writerow([r.time, r.id, r.source, r.dest])
    else:
        io_utils.write_requests(requests, args.output)
        print(f"Wrote {len(requests)} requests to {args.output}", file=sys.stderr)
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    from . import experiments

    experiments.main(Path(args.out), seeds=args.seeds, plots=not args.no_plots, quick=args.quick)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m elevator_sim",
        description="Discrete-time simulation of a destination-dispatch elevator system.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="simulate a request file")
    run.add_argument("input", help="CSV with columns time,id,source,dest ('-' for stdin)")
    run.add_argument("--out", default="output", help="directory for output files (default: output)")
    run.add_argument("--plots", action="store_true", help="also save charts (needs matplotlib)")
    _add_building_args(run, floors_default=None)
    _add_scheduler_args(run)
    run.set_defaults(func=cmd_run)

    gen = sub.add_parser("generate", help="write a synthetic request file")
    gen.add_argument("--pattern", choices=PATTERNS, default="up_peak")
    gen.add_argument("--floors", type=int, default=40)
    gen.add_argument("--lowest-floor", type=int, default=1)
    gen.add_argument("--duration", type=int, default=600, help="ticks of arrivals (default: 600)")
    gen.add_argument("--rate", type=float, default=0.3, help="mean arrivals per tick (default: 0.3)")
    gen.add_argument("--seed", type=int, default=None)
    gen.add_argument("-o", "--output", default="-", help="output CSV path (default: stdout)")
    gen.set_defaults(func=cmd_generate)

    cmp_ = sub.add_parser("compare", help="run the scheduler, fairness and express experiments")
    cmp_.add_argument("--out", default="results", help="directory for tables and charts (default: results)")
    cmp_.add_argument("--seeds", type=int, default=3, help="random traffic seeds per scenario (default: 3)")
    cmp_.add_argument("--quick", action="store_true", help="smaller experiments for a fast check")
    cmp_.add_argument("--no-plots", action="store_true", help="skip charts")
    cmp_.set_defaults(func=cmd_compare)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ValueError as exc:
        parser.exit(2, f"error: {exc}\n")
        return 2
