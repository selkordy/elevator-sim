"""Reading requests and writing simulation output files."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, List, TextIO, Union

from .models import Request
from .simulation import SimResult

PathLike = Union[str, Path]
REQUIRED = ("time", "id", "source", "dest")


def parse_requests(rows: Iterable[dict]) -> List[Request]:
    requests = []
    for n, row in enumerate(rows, start=2):  # line 1 is the header
        try:
            requests.append(
                Request(
                    time=int(row["time"]),
                    id=row["id"].strip(),
                    source=int(row["source"]),
                    dest=int(row["dest"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"line {n}: bad request row {row!r} ({exc})") from None
    return requests


def read_requests(source: Union[PathLike, TextIO]) -> List[Request]:
    """Read ``time,id,source,dest`` rows from a CSV path or open file."""
    if isinstance(source, (str, Path)):
        with open(source, newline="") as f:
            return read_requests(f)
    reader = csv.DictReader((line for line in source if line.strip()), skipinitialspace=True)
    missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"input is missing columns: {', '.join(missing)}")
    return parse_requests(reader)


def write_requests(requests: Iterable[Request], path: PathLike) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(REQUIRED)
        for r in requests:
            w.writerow([r.time, r.id, r.source, r.dest])


def write_positions(result: SimResult, path: PathLike) -> None:
    """One row per tick: ``time,elevator_0,elevator_1,...`` (floor numbers)."""
    n = result.config.num_elevators
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time"] + [f"elevator_{i}" for i in range(n)])
        for t, row in enumerate(result.positions):
            w.writerow([t] + row)


def write_passengers(result: SimResult, path: PathLike) -> None:
    """Per-passenger timings, handy for further analysis."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "id",
                "request_time",
                "source",
                "dest",
                "elevator",
                "pickup_time",
                "dropoff_time",
                "wait_time",
                "travel_time",
                "total_time",
            ]
        )
        for p in sorted(result.passengers, key=lambda p: (p.request_time, p.id)):
            w.writerow(
                [
                    p.id,
                    p.request_time,
                    p.source,
                    p.dest,
                    p.elevator,
                    p.pickup_time,
                    p.dropoff_time,
                    p.wait_time,
                    p.travel_time,
                    p.total_time,
                ]
            )
