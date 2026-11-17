"""A single elevator car and its movement rules.

Each tick a car does three things, in order:

1. **Alight** – riders whose destination is the current floor get off.
2. **Board**  – assigned riders waiting on this floor get on, if there is room
   and their direction is compatible (see :meth:`Elevator._may_board`).
3. **Move**   – the car moves one floor following the LOOK rule: keep going in
   the current direction while there is a stop ahead, otherwise turn around,
   otherwise go idle.

The same :meth:`step` code drives both the real simulation and the
look-ahead projections the destination-dispatch scheduler uses to price a
new assignment, so the scheduler's cost model is the car's actual behaviour,
not an approximation of it.
"""

from __future__ import annotations

from typing import FrozenSet, List, Optional

from .models import Direction, Passenger


class Elevator:
    __slots__ = (
        "id",
        "floor",
        "direction",
        "capacity",
        "served",
        "onboard",
        "pending",
        "hold_until",
        "floors_travelled",
        "stops",
        "busy_ticks",
    )

    def __init__(
        self,
        id: int,
        floor: int,
        capacity: int,
        served: Optional[FrozenSet[int]] = None,
    ) -> None:
        self.id = id
        self.floor = floor
        self.direction = Direction.IDLE
        self.capacity = capacity
        self.served = served
        self.onboard: List[Passenger] = []
        # Assigned but not yet picked up, kept in request order (FIFO).
        self.pending: List[Passenger] = []
        # The car may not leave its floor before this tick (doors open).
        self.hold_until = 0
        # Bookkeeping for statistics.
        self.floors_travelled = 0
        self.stops = 0
        self.busy_ticks = 0

    # ------------------------------------------------------------------ state

    @property
    def load(self) -> int:
        return len(self.onboard)

    @property
    def is_idle(self) -> bool:
        return not self.onboard and not self.pending

    def assign(self, passenger: Passenger) -> None:
        passenger.elevator = self.id
        self.pending.append(passenger)

    def clone(self) -> "Elevator":
        car = Elevator(self.id, self.floor, self.capacity, self.served)
        car.direction = self.direction
        car.onboard = [p.clone() for p in self.onboard]
        car.pending = [p.clone() for p in self.pending]
        car.hold_until = self.hold_until
        return car

    # ------------------------------------------------------------- behaviour

    def _vip(self, now: int, threshold: int) -> Optional[Passenger]:
        """The oldest waiting rider, if they have waited past the threshold.

        ``pending`` is in request order, so the first starving rider is the
        oldest one overall."""
        for p in self.pending:
            if now - p.request_time >= threshold:
                return p
        return None

    def _on_the_way(self, p: Passenger, vip: Passenger) -> bool:
        """True if ``p`` would ride toward the VIP and get off no later than
        the VIP's floor, so carrying them can never take the VIP's spot."""
        toward = vip.source - self.floor
        if toward == 0 or (toward > 0) != (p.direction == Direction.UP):
            return False
        return (vip.source - p.dest) * toward >= 0

    def _targets(self, vip: Optional[Passenger]) -> List[int]:
        """Floors the car still has to visit. While a VIP is waiting the car
        only heads for them and for its riders' destinations."""
        drops = [p.dest for p in self.onboard]
        if vip is not None:
            return drops + [vip.source]
        return drops + [p.source for p in self.pending]

    def _stop_ahead(self, targets: List[int]) -> bool:
        if self.direction == Direction.UP:
            return any(t > self.floor for t in targets)
        if self.direction == Direction.DOWN:
            return any(t < self.floor for t in targets)
        return False

    def _may_board(self, p: Passenger, targets: List[int]) -> bool:
        """Direction rule: a rider boards if the car is idle, already heading
        their way, or about to turn around because nothing is left ahead."""
        if self.direction == Direction.IDLE or p.direction == self.direction:
            return True
        return not self._stop_ahead(targets)

    def step(self, now: int, stop_time: int, starvation_threshold: int) -> List[Passenger]:
        """Advance the car by one tick. Returns riders delivered this tick."""
        stopped = False

        # 1. Alight.
        delivered = [p for p in self.onboard if p.dest == self.floor]
        if delivered:
            self.onboard = [p for p in self.onboard if p.dest != self.floor]
            for p in delivered:
                p.dropoff_time = now
            stopped = True

        # 2. Board, oldest request first. If someone has been waiting too long
        #    (the VIP), only take riders who will be off before reaching them.
        vip = self._vip(now, starvation_threshold)
        boarded = False
        for p in list(self.pending):
            if p.source != self.floor:
                continue
            if len(self.onboard) >= self.capacity:
                break
            if vip is not None and p is not vip and not self._on_the_way(p, vip):
                continue
            if not self._may_board(p, self._targets(vip)):
                continue
            self.pending.remove(p)
            self.onboard.append(p)
            p.pickup_time = now
            boarded = True
            if self.direction == Direction.IDLE or p.direction != self.direction:
                self.direction = p.direction
            if p is vip:
                vip = self._vip(now, starvation_threshold)
        if boarded:
            stopped = True

        if stopped:
            self.stops += 1
            if stop_time:
                self.hold_until = max(self.hold_until, now + stop_time)

        if not self.is_idle:
            self.busy_ticks += 1

        # 3. Move (LOOK).
        if now < self.hold_until:
            return delivered
        targets = self._targets(vip)
        if not targets:
            self.direction = Direction.IDLE
            return delivered
        above = any(t > self.floor for t in targets)
        below = any(t < self.floor for t in targets)
        if self.direction == Direction.UP and above:
            move = 1
        elif self.direction == Direction.DOWN and below:
            move = -1
        elif above and below:
            # Idle or turning with work on both sides: head for the nearest.
            nearest = min(targets, key=lambda t: (abs(t - self.floor), t))
            move = 1 if nearest > self.floor else -1
        elif above:
            move = 1
        elif below:
            move = -1
        else:
            # Only work left is on this floor; go idle so it can board next tick.
            self.direction = Direction.IDLE
            return delivered
        self.floor += move
        self.direction = Direction(move)
        self.floors_travelled += 1
        return delivered

    def project(
        self,
        now: int,
        stop_time: int,
        starvation_threshold: int,
        extra: Optional[Passenger] = None,
        max_ticks: int = 100_000,
    ) -> List[Passenger]:
        """Run a copy of this car forward with no new requests (plus ``extra``
        if given) until it is empty. Returns cloned riders with projected
        pickup and dropoff times. Uses only the current state, so it never
        looks ahead in the request list."""
        car = self.clone()
        if extra is not None:
            car.pending.append(extra.clone())
        done: List[Passenger] = []
        t = now
        while car.onboard or car.pending:
            done.extend(car.step(t, stop_time, starvation_threshold))
            t += 1
            if t - now > max_ticks:
                raise RuntimeError(f"projection for car {self.id} did not finish")
        return done
