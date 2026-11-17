"""Synthetic traffic and building layouts for experiments."""

from __future__ import annotations

import random
from typing import FrozenSet, List, Optional

from .models import Request

PATTERNS = ("up_peak", "down_peak", "lunch", "interfloor")

# Share of trips (from lobby, to lobby, between upper floors) per pattern.
MIX = {
    "up_peak": (0.85, 0.05, 0.10),  # morning arrivals
    "down_peak": (0.05, 0.85, 0.10),  # evening departures
    "lunch": (0.40, 0.40, 0.20),  # two-way rush
    "interfloor": (0.10, 0.10, 0.80),  # meetings between floors
}


def generate(
    pattern: str,
    *,
    num_floors: int,
    duration: int,
    rate: float,
    lowest_floor: int = 1,
    seed: Optional[int] = None,
) -> List[Request]:
    """Poisson arrivals at ``rate`` passengers per tick for ``duration`` ticks.

    Floor ``lowest_floor`` is the lobby. Upper floors are chosen uniformly.
    """
    if pattern not in MIX:
        raise ValueError(f"unknown pattern {pattern!r}; choose from {PATTERNS}")
    if rate <= 0 or duration <= 0:
        raise ValueError("rate and duration must be positive")
    rng = random.Random(seed)
    lobby = lowest_floor
    upper = list(range(lowest_floor + 1, lowest_floor + num_floors))
    from_lobby, to_lobby, _ = MIX[pattern]

    requests: List[Request] = []
    t = rng.expovariate(rate)
    n = 0
    while t < duration:
        u = rng.random()
        if u < from_lobby:
            source, dest = lobby, rng.choice(upper)
        elif u < from_lobby + to_lobby:
            source, dest = rng.choice(upper), lobby
        else:
            source, dest = rng.sample(upper, 2)
        n += 1
        requests.append(Request(int(t), f"p{n}", source, dest))
        t += rng.expovariate(rate)
    return requests


def express_layout(
    num_floors: int,
    num_elevators: int,
    express_cars: int,
    express_from: Optional[int] = None,
    lowest_floor: int = 1,
) -> List[Optional[FrozenSet[int]]]:
    """The last ``express_cars`` cars serve only the lobby and floors from
    ``express_from`` up; they skip the floors in between. The other cars are
    locals that stop everywhere, so every trip is still possible without a
    transfer."""
    if not 0 <= express_cars < num_elevators:
        raise ValueError("need at least one local car: 0 <= express_cars < num_elevators")
    top = lowest_floor + num_floors - 1
    if express_from is None:
        express_from = lowest_floor + num_floors // 2
    if not lowest_floor < express_from <= top:
        raise ValueError("express_from must be above the lobby and inside the building")
    express = frozenset([lowest_floor, *range(express_from, top + 1)])
    return [None] * (num_elevators - express_cars) + [express] * express_cars


def banked_layout(
    num_floors: int,
    low_cars: int,
    high_cars: int,
    local_cars: int = 1,
    split: Optional[int] = None,
    lowest_floor: int = 1,
) -> List[Optional[FrozenSet[int]]]:
    """Classic low-rise / high-rise banks. Low-rise cars serve the lobby up to
    ``split - 1``; high-rise cars serve the lobby and ``split`` upward, running
    express through the low zone. ``local_cars`` serve every floor so trips
    between the two zones are still possible without a transfer."""
    if local_cars < 1:
        raise ValueError("need at least one all-floors car for cross-zone trips")
    top = lowest_floor + num_floors - 1
    if split is None:
        split = lowest_floor + num_floors // 2
    low = frozenset(range(lowest_floor, split))
    high = frozenset([lowest_floor, *range(split, top + 1)])
    return [None] * local_cars + [low] * low_cars + [high] * high_cars


def parse_floor_spec(spec: str) -> FrozenSet[int]:
    """``"1,30-60"`` -> {1, 30, 31, ..., 60}."""
    floors = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part[1:]:
            i = part.index("-", 1)
            a, b = int(part[:i]), int(part[i + 1 :])
            floors.update(range(min(a, b), max(a, b) + 1))
        else:
            floors.add(int(part))
    return frozenset(floors)
