"""Dispatch strategies.

A scheduler is called once per request, at the tick the request appears,
and must return the index of the car that will serve it. That choice is
final, which matches destination dispatch: the lobby screen tells the
passenger which car to wait for.

Schedulers only receive the arriving passenger and the cars' current state,
so none of them can see future requests.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Sequence, Type

from .elevator import Elevator
from .models import Direction, Passenger, SimConfig


class Scheduler(ABC):
    name = "base"

    def reset(self, config: SimConfig) -> None:
        """Called once before a simulation starts."""

    @abstractmethod
    def assign(
        self,
        passenger: Passenger,
        elevators: Sequence[Elevator],
        config: SimConfig,
        now: int,
    ) -> int:
        """Return the id of the car that will serve ``passenger``."""

    @staticmethod
    def eligible(
        passenger: Passenger, elevators: Sequence[Elevator], config: SimConfig
    ) -> List[Elevator]:
        cars = [
            c for c in elevators if config.can_serve(c.id, passenger.source, passenger.dest)
        ]
        if not cars:
            raise ValueError(f"no car serves {passenger.source} -> {passenger.dest}")
        return cars


# ---------------------------------------------------------------- baselines


class RoundRobin(Scheduler):
    """Hand requests to cars in turn, ignoring where the cars are."""

    name = "round_robin"

    def reset(self, config: SimConfig) -> None:
        self._next = 0

    def assign(self, passenger, elevators, config, now):
        n = len(elevators)
        for k in range(n):
            car_id = (self._next + k) % n
            if config.can_serve(car_id, passenger.source, passenger.dest):
                self._next = car_id + 1
                return car_id
        raise ValueError(f"no car serves {passenger.source} -> {passenger.dest}")


def eta_to_pickup(car: Elevator, passenger: Passenger) -> int:
    """Floors a car must travel before it can pick ``passenger`` up heading
    their way, assuming it finishes its current sweep first (LOOK)."""
    sign = -1 if car.direction == Direction.DOWN else 1
    # Mirror the building for a downward car so only the "up" case is coded.
    here = car.floor * sign
    src = passenger.source * sign
    wants_same = passenger.direction * sign > 0
    targets = [p.dest * sign for p in car.onboard]
    for p in car.pending:  # riders it will pick up, and where it will take them
        targets += [p.source * sign, p.dest * sign]

    if car.direction == Direction.IDLE:
        return abs(car.floor - passenger.source)
    if src >= here and wants_same:
        return src - here
    top = max(targets + [here, src])
    if not wants_same:
        return (top - here) + (top - src)
    bottom = min(targets + [src])
    return (top - here) + (top - bottom) + (src - bottom)


def nearest_car_cost(car: Elevator, passenger: Passenger, config: SimConfig) -> float:
    cost = float(eta_to_pickup(car, passenger))
    committed = car.load + len(car.pending)
    if committed >= car.capacity:
        # Likely full on arrival: penalise by one extra trip across the building.
        cost += config.num_floors
    return cost


class NearestCar(Scheduler):
    """Classic collective control: the car that can reach the caller soonest,
    taking its current direction and remaining sweep into account."""

    name = "nearest_car"

    def assign(self, passenger, elevators, config, now):
        cars = self.eligible(passenger, elevators, config)
        best = min(
            cars,
            key=lambda c: (nearest_car_cost(c, passenger, config), c.load + len(c.pending), c.id),
        )
        return best.id


class ZoneBased(Scheduler):
    """Split the building into contiguous floor bands and give each band its
    own cars. A trip belongs to the zone of its non-lobby end, which keeps a
    car's stops clustered. Within a zone, pick the nearest car."""

    name = "zone"

    def __init__(self, zones: Optional[int] = None) -> None:
        self.zones = zones

    def reset(self, config: SimConfig) -> None:
        z = self.zones or max(1, config.num_elevators // 2)
        self._z = max(1, min(z, config.num_elevators))
        self._band = config.num_floors / self._z

    def zone_of(self, passenger: Passenger, config: SimConfig) -> int:
        lobby = config.lowest_floor
        key = passenger.dest if passenger.source == lobby else passenger.source
        return min(self._z - 1, int((key - lobby) / self._band))

    def assign(self, passenger, elevators, config, now):
        cars = self.eligible(passenger, elevators, config)
        zone = self.zone_of(passenger, config)
        in_zone = [c for c in cars if c.id % self._z == zone] or cars
        best = min(
            in_zone,
            key=lambda c: (nearest_car_cost(c, passenger, config), c.load + len(c.pending), c.id),
        )
        return best.id


# ------------------------------------------------------- destination dispatch


class DestinationDispatch(Scheduler):
    """Cost-based destination dispatch.

    For every car that can serve the trip, run a copy of that car forward
    (same movement code as the real car, no future requests) twice: once as
    it is, once with the new passenger added. The difference is the extra
    cost this assignment causes, counting the delay it imposes on riders the
    car already has. Pick the car with the smallest extra cost.

    Cost is ``sum(total_time ** fairness)`` over the car's riders, plus an
    optional ``busy_weight`` x (ticks until the car is free again):

    * ``fairness = 1`` minimises the plain sum of total times (efficiency).
    * ``fairness > 1`` makes long trips disproportionately expensive, which
      shortens the tail. The default of 2 also lowered the mean in our tests,
      because it stops the scheduler from parking a request behind a long
      queue just because that looked marginally cheapest right now.
    * ``busy_weight`` charges for keeping a car occupied (an opportunity cost
      for future callers). Tested and left at 0: it did not pay off.
    """

    name = "destination_dispatch"
    # p=2 beat p=1 on both mean and tail in every scenario tested (see README).
    DEFAULT_FAIRNESS = 2.0
    DEFAULT_BUSY_WEIGHT = 0.0

    def __init__(self, fairness: float = DEFAULT_FAIRNESS, busy_weight: float = DEFAULT_BUSY_WEIGHT) -> None:
        if fairness < 1:
            raise ValueError("fairness exponent must be >= 1")
        if busy_weight < 0:
            raise ValueError("busy_weight must be >= 0")
        self.fairness = fairness
        self.busy_weight = busy_weight
        tags = []
        if fairness != self.DEFAULT_FAIRNESS:
            tags.append(f"p={fairness:g}")
        if busy_weight:
            tags.append(f"busy={busy_weight:g}")
        if tags:
            self.name = f"destination_dispatch({', '.join(tags)})"

    def _cost(self, riders: List[Passenger], now: int) -> float:
        p = self.fairness
        total = 0.0
        finish = now
        for r in riders:
            t = r.total_time or 0
            total += t if p == 1.0 else float(t) ** p
            finish = max(finish, r.dropoff_time or now)
        # Opportunity cost: a car that stays busy longer is unavailable to
        # people who have not called yet.
        return total + self.busy_weight * (finish - now)

    def assign(self, passenger, elevators, config, now):
        cars = self.eligible(passenger, elevators, config)
        thr = config.starvation_threshold or 0
        best_id, best_key = None, None
        for car in cars:
            before = self._cost(car.project(now, config.stop_time, thr), now) if not car.is_idle else 0.0
            after = self._cost(car.project(now, config.stop_time, thr, extra=passenger), now)
            key = (after - before, car.load + len(car.pending), car.id)
            if best_key is None or key < best_key:
                best_id, best_key = car.id, key
        assert best_id is not None
        return best_id


SCHEDULERS: Dict[str, Type[Scheduler]] = {
    "destination_dispatch": DestinationDispatch,
    "nearest_car": NearestCar,
    "zone": ZoneBased,
    "round_robin": RoundRobin,
}


def make_scheduler(name: str, **kwargs) -> Scheduler:
    try:
        cls = SCHEDULERS[name]
    except KeyError:
        raise ValueError(f"unknown scheduler {name!r}; choose from {sorted(SCHEDULERS)}") from None
    if cls is DestinationDispatch:
        return DestinationDispatch(
            fairness=kwargs.get("fairness", DestinationDispatch.DEFAULT_FAIRNESS),
            busy_weight=kwargs.get("busy_weight", DestinationDispatch.DEFAULT_BUSY_WEIGHT),
        )
    if cls is ZoneBased:
        return ZoneBased(zones=kwargs.get("zones"))
    return cls()
