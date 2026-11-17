"""Core data types: passengers and simulation configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import FrozenSet, List, Optional, Sequence


class Direction(IntEnum):
    DOWN = -1
    IDLE = 0
    UP = 1


@dataclass
class Passenger:
    """One destination-dispatch request and everything that happens to it.

    Times are integer simulation ticks:
      wait_time   = pickup_time  - request_time
      travel_time = dropoff_time - pickup_time
      total_time  = dropoff_time - request_time
    """

    id: str
    request_time: int
    source: int
    dest: int
    elevator: Optional[int] = None
    pickup_time: Optional[int] = None
    dropoff_time: Optional[int] = None

    @property
    def direction(self) -> Direction:
        return Direction.UP if self.dest > self.source else Direction.DOWN

    @property
    def wait_time(self) -> Optional[int]:
        if self.pickup_time is None:
            return None
        return self.pickup_time - self.request_time

    @property
    def travel_time(self) -> Optional[int]:
        if self.dropoff_time is None or self.pickup_time is None:
            return None
        return self.dropoff_time - self.pickup_time

    @property
    def total_time(self) -> Optional[int]:
        if self.dropoff_time is None:
            return None
        return self.dropoff_time - self.request_time

    def clone(self) -> "Passenger":
        return Passenger(
            self.id,
            self.request_time,
            self.source,
            self.dest,
            self.elevator,
            self.pickup_time,
            self.dropoff_time,
        )


@dataclass
class Request:
    """A raw input row: ``time,id,source,dest``."""

    time: int
    id: str
    source: int
    dest: int


@dataclass
class SimConfig:
    """Building and fleet configuration.

    Floors are numbered ``lowest_floor .. lowest_floor + num_floors - 1``.
    ``served_floors[i]`` restricts elevator *i* to a subset of floors (express
    cars); ``None`` means the car serves every floor.
    """

    num_floors: int = 60
    num_elevators: int = 4
    capacity: int = 10
    lowest_floor: int = 1
    # Ticks the car stays at a floor after a stop (doors open/close).
    # 0 matches the spec literally: only travel consumes time.
    stop_time: int = 0
    # A waiting passenger older than this gets priority: their car stops
    # taking on new riders until it has picked them up. Guarantees that
    # every request is eventually served, even under heavy overload.
    starvation_threshold: Optional[int] = None
    # Floor each car starts on (defaults to the lobby / lowest floor).
    start_floors: Optional[Sequence[int]] = None
    served_floors: Optional[Sequence[Optional[FrozenSet[int]]]] = None
    max_ticks: int = 1_000_000
    _floor_set: FrozenSet[int] = field(init=False, repr=False, default=frozenset())

    def __post_init__(self) -> None:
        if self.num_floors < 2:
            raise ValueError("num_floors must be at least 2")
        if not 1 <= self.num_elevators:
            raise ValueError("num_elevators must be at least 1")
        if self.capacity < 1:
            raise ValueError("capacity must be at least 1")
        if self.stop_time < 0:
            raise ValueError("stop_time must be >= 0")
        if self.starvation_threshold is None:
            self.starvation_threshold = 3 * self.num_floors
        self._floor_set = frozenset(range(self.lowest_floor, self.highest_floor + 1))

        if self.start_floors is None:
            self.start_floors = [self.lowest_floor] * self.num_elevators
        if len(self.start_floors) != self.num_elevators:
            raise ValueError("start_floors must have one entry per elevator")
        for f in self.start_floors:
            self.check_floor(f)

        if self.served_floors is None:
            self.served_floors = [None] * self.num_elevators
        if len(self.served_floors) != self.num_elevators:
            raise ValueError("served_floors must have one entry per elevator")
        normalised = []
        for i, floors in enumerate(self.served_floors):
            if floors is None or set(floors) >= self._floor_set:
                normalised.append(None)
                continue
            floors = frozenset(floors)
            bad = floors - self._floor_set
            if bad:
                raise ValueError(f"elevator {i} serves floors outside the building: {sorted(bad)}")
            if len(floors) < 2:
                raise ValueError(f"elevator {i} must serve at least two floors")
            normalised.append(floors)
        self.served_floors = normalised
        self._served: List[Optional[FrozenSet[int]]] = normalised
        self._check_coverage()

    @property
    def highest_floor(self) -> int:
        return self.lowest_floor + self.num_floors - 1

    def check_floor(self, floor: int) -> None:
        if floor not in self._floor_set:
            raise ValueError(
                f"floor {floor} is outside the building "
                f"({self.lowest_floor}..{self.highest_floor})"
            )

    def serves(self, elevator: int, floor: int) -> bool:
        floors = self._served[elevator]
        return floors is None or floor in floors

    def served_set(self, elevator: int) -> Optional[FrozenSet[int]]:
        return self._served[elevator]

    def can_serve(self, elevator: int, source: int, dest: int) -> bool:
        return self.serves(elevator, source) and self.serves(elevator, dest)

    def _check_coverage(self) -> None:
        """Every origin/destination pair must be reachable without a transfer."""
        if any(f is None for f in self._served):
            return
        floors = sorted(self._floor_set)
        for a in floors:
            for b in floors:
                if a != b and not any(
                    self.can_serve(e, a, b) for e in range(self.num_elevators)
                ):
                    raise ValueError(
                        f"no elevator serves the trip {a} -> {b}; give at least "
                        "one car every floor or make the express sets overlap"
                    )
