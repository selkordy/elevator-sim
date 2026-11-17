"""Discrete-time simulation loop.

Order of events inside tick ``t``:

1. Log every car's floor at time ``t``.
2. Release requests whose ``time == t`` (never later ones: the scheduler
   cannot see the future) and assign each one to a car immediately.
3. Step every car: alight, board, move one floor.
4. Stop once every request has been released and delivered; otherwise go
   to ``t + 1``. Time always advances one tick at a time, including through
   quiet stretches where no requests arrive.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Iterable, List, Optional

from .elevator import Elevator
from .models import Passenger, Request, SimConfig
from .schedulers import Scheduler


@dataclass
class SimResult:
    config: SimConfig
    scheduler: str
    passengers: List[Passenger]
    # positions[t][i] = floor of car i at tick t
    positions: List[List[int]]
    # loads[t][i] = riders in car i at tick t
    loads: List[List[int]]
    # waiting[t] = requests assigned but not yet picked up at tick t
    waiting: List[int]
    elevators: List[Elevator]
    skipped: List[str] = field(default_factory=list)

    @property
    def ticks(self) -> int:
        return len(self.positions)


class Simulation:
    def __init__(
        self,
        config: SimConfig,
        scheduler: Scheduler,
        requests: Iterable[Request],
    ) -> None:
        self.config = config
        self.scheduler = scheduler
        self.scheduler.reset(config)
        self.elevators = [
            Elevator(i, config.start_floors[i], config.capacity, config.served_set(i))  # type: ignore[index]
            for i in range(config.num_elevators)
        ]
        self.skipped: List[str] = []
        self._queue: Deque[Request] = deque(self._validate(requests))
        self.passengers: List[Passenger] = []
        self.now = 0

    # ----------------------------------------------------------- validation

    def _validate(self, requests: Iterable[Request]) -> List[Request]:
        seen: Dict[str, Request] = {}
        valid: List[Request] = []
        for r in requests:
            if r.id in seen:
                raise ValueError(f"duplicate passenger id {r.id!r}")
            seen[r.id] = r
            if r.time < 0:
                raise ValueError(f"{r.id}: request time must be >= 0")
            self.config.check_floor(r.source)
            self.config.check_floor(r.dest)
            if r.source == r.dest:
                self.skipped.append(f"{r.id}: source equals destination ({r.source}); ignored")
                continue
            if not any(
                self.config.can_serve(e, r.source, r.dest)
                for e in range(self.config.num_elevators)
            ):
                raise ValueError(f"{r.id}: no elevator serves {r.source} -> {r.dest}")
            valid.append(r)
        # Stable sort: rows with the same time keep their file order.
        valid.sort(key=lambda r: r.time)
        return valid

    # ------------------------------------------------------------------ run

    def run(self) -> SimResult:
        cfg = self.config
        positions: List[List[int]] = []
        loads: List[List[int]] = []
        waiting: List[int] = []
        threshold = cfg.starvation_threshold or 0

        while True:
            positions.append([car.floor for car in self.elevators])
            loads.append([car.load for car in self.elevators])

            # Release only requests that exist at this tick.
            while self._queue and self._queue[0].time <= self.now:
                r = self._queue.popleft()
                p = Passenger(r.id, self.now, r.source, r.dest)
                car_id = self.scheduler.assign(p, self.elevators, cfg, self.now)
                if not cfg.can_serve(car_id, p.source, p.dest):
                    raise RuntimeError(
                        f"{self.scheduler.name} assigned {p.id} to car {car_id}, "
                        "which does not serve that trip"
                    )
                self.elevators[car_id].assign(p)
                self.passengers.append(p)

            waiting.append(sum(len(car.pending) for car in self.elevators))

            for car in self.elevators:
                car.step(self.now, cfg.stop_time, threshold)
                if car.load > car.capacity:  # pragma: no cover - invariant
                    raise RuntimeError(f"car {car.id} over capacity at t={self.now}")

            if not self._queue and all(car.is_idle for car in self.elevators):
                break
            self.now += 1
            if self.now > cfg.max_ticks:
                raise RuntimeError(f"simulation exceeded max_ticks={cfg.max_ticks}")

        return SimResult(
            config=cfg,
            scheduler=self.scheduler.name,
            passengers=self.passengers,
            positions=positions,
            loads=loads,
            waiting=waiting,
            elevators=self.elevators,
            skipped=self.skipped,
        )


def simulate(
    config: SimConfig,
    scheduler: Scheduler,
    requests: Iterable[Request],
) -> SimResult:
    return Simulation(config, scheduler, requests).run()
