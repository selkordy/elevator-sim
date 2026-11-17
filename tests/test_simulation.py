import random

import pytest

from elevator_sim import (
    DestinationDispatch,
    Elevator,
    Passenger,
    Request,
    SimConfig,
    make_scheduler,
    simulate,
)
from elevator_sim.schedulers import SCHEDULERS, Scheduler
from elevator_sim.traffic import banked_layout, express_layout, generate


def R(t, pid, s, d):
    return Request(t, pid, s, d)


def one_car(floors=20, **kw):
    return SimConfig(num_floors=floors, num_elevators=1, **kw)


def by_id(result):
    return {p.id: p for p in result.passengers}


# ------------------------------------------------------------- basic timing


def test_single_rider_from_lobby():
    res = simulate(one_car(), make_scheduler("destination_dispatch"), [R(0, "a", 1, 10)])
    a = by_id(res)["a"]
    assert (a.pickup_time, a.dropoff_time) == (0, 9)
    assert (a.wait_time, a.travel_time, a.total_time) == (0, 9, 9)
    assert res.positions[0] == [1] and res.positions[9] == [10]
    assert res.ticks == 10


def test_car_travels_to_pick_up():
    res = simulate(one_car(), make_scheduler("destination_dispatch"), [R(0, "a", 5, 1)])
    a = by_id(res)["a"]
    assert (a.wait_time, a.travel_time) == (4, 4)


def test_ticks_one_at_a_time_through_gaps():
    res = simulate(one_car(), make_scheduler("destination_dispatch"), [R(20, "a", 1, 3)])
    a = by_id(res)["a"]
    assert (a.pickup_time, a.dropoff_time) == (20, 22)
    assert res.ticks == 23  # t = 0..22, every tick logged
    assert all(row == [1] for row in res.positions[:21])


def test_spec_example():
    reqs = [R(0, "passenger1", 1, 51), R(0, "passenger2", 1, 37), R(10, "passenger3", 20, 1)]
    cfg = SimConfig(num_floors=51, num_elevators=2)
    res = simulate(cfg, make_scheduler("destination_dispatch"), reqs)
    assert all(p.dropoff_time is not None for p in res.passengers)
    for p in res.passengers:
        assert p.travel_time >= abs(p.dest - p.source)


def test_stop_time_holds_the_car():
    res = simulate(one_car(stop_time=2), make_scheduler("destination_dispatch"), [R(0, "a", 1, 3)])
    a = by_id(res)["a"]
    # Boards at t=0, doors hold through t=1, moves at t=2 and t=3, arrives t=4.
    assert (a.pickup_time, a.dropoff_time) == (0, 4)
    assert [row[0] for row in res.positions] == [1, 1, 1, 2, 3]


def test_requests_out_of_order_are_sorted():
    reqs = [R(5, "late", 1, 2), R(0, "early", 1, 2)]
    res = simulate(one_car(), make_scheduler("destination_dispatch"), reqs)
    assert by_id(res)["early"].pickup_time == 0
    assert by_id(res)["late"].request_time == 5


# ---------------------------------------------------------- direction rules


def test_car_does_not_pick_up_against_its_direction():
    # The car carries 'up' to 10. It passes 'down' (5 -> 2) on the way up and
    # must come back for them after turning around at 10.
    reqs = [R(0, "up", 1, 10), R(0, "down", 5, 2)]
    res = simulate(one_car(), make_scheduler("destination_dispatch"), reqs)
    up, down = by_id(res)["up"], by_id(res)["down"]
    assert up.dropoff_time == 9
    assert down.pickup_time == 14  # 9 + (10 - 5)
    assert down.dropoff_time == 17


def test_same_direction_rider_is_picked_up_on_the_way():
    reqs = [R(0, "a", 1, 10), R(0, "b", 5, 8)]
    res = simulate(one_car(), make_scheduler("destination_dispatch"), reqs)
    b = by_id(res)["b"]
    assert (b.pickup_time, b.dropoff_time) == (4, 7)


# --------------------------------------------------------------- invariants


@pytest.mark.parametrize("scheduler", sorted(SCHEDULERS))
@pytest.mark.parametrize("pattern", ["up_peak", "down_peak", "lunch", "interfloor"])
def test_invariants_under_heavy_load(scheduler, pattern):
    cfg = SimConfig(num_floors=25, num_elevators=3, capacity=3, stop_time=1)
    reqs = generate(pattern, num_floors=25, duration=150, rate=1.2, seed=3)
    res = simulate(cfg, make_scheduler(scheduler), reqs)

    assert len(res.passengers) == len(reqs)
    assert all(p.dropoff_time is not None for p in res.passengers), "everyone is delivered"
    for prev, cur in zip(res.positions, res.positions[1:]):
        assert all(abs(a - b) <= 1 for a, b in zip(prev, cur)), "one floor per tick"
    for row in res.positions:
        assert all(cfg.lowest_floor <= f <= cfg.highest_floor for f in row)
    for row in res.loads:
        assert all(load <= cfg.capacity for load in row), "capacity respected"
    for p in res.passengers:
        assert p.request_time <= p.pickup_time < p.dropoff_time
        assert p.travel_time >= abs(p.dest - p.source)
        # The car really was at the source when it picked up, and at the dest at dropoff.
        assert res.positions[p.pickup_time][p.elevator] == p.source
        assert res.positions[p.dropoff_time][p.elevator] == p.dest


def _starvation_case(threshold):
    # One car of capacity 1 and a lobby crowd arriving every tick for 1 -> 10.
    # 'stuck' wants 5 -> 8. Every time the car passes floor 5 going up it is
    # full of lobby riders, and going down it is heading the wrong way.
    reqs = [R(t, f"lobby{t}", 1, 10) for t in range(200)] + [R(1, "stuck", 5, 8)]
    cfg = one_car(floors=12, capacity=1, starvation_threshold=threshold)
    return by_id(simulate(cfg, make_scheduler("destination_dispatch"), reqs))["stuck"]


def test_without_the_guard_a_rider_can_starve():
    stuck = _starvation_case(threshold=10**9)
    assert stuck.wait_time > 150  # only served once the lobby crowd dries up


def test_starvation_guard_bounds_the_wait():
    stuck = _starvation_case(threshold=40)
    # Becomes the VIP right after lobby1 boards; then at most a couple of sweeps.
    assert stuck.wait_time <= 40 + 4 * 12


def test_vip_only_shares_the_car_with_riders_on_the_way():
    car = Elevator(0, floor=1, capacity=5)
    car.assign(Passenger("vip", 0, 10, 1))
    car.assign(Passenger("beyond", 50, 1, 12))  # would still be aboard at floor 10
    car.assign(Passenger("short", 50, 1, 5))  # off before the car reaches floor 10
    car.step(now=60, stop_time=0, starvation_threshold=30)
    assert [p.id for p in car.onboard] == ["short"]
    assert car.floor == 2


# ---------------------------------------------------------------- scheduler


class Spy(Scheduler):
    name = "spy"

    def __init__(self):
        self.calls = []

    def assign(self, passenger, elevators, config, now):
        self.calls.append((now, passenger.id, passenger.request_time))
        return 0


def test_scheduler_never_sees_future_requests():
    reqs = [R(t, f"p{i}", 1, 2 + t % 5) for i, t in enumerate((0, 3, 3, 9, 40))]
    spy = Spy()
    simulate(SimConfig(num_floors=10, num_elevators=2), spy, reqs)
    assert [c[0] for c in spy.calls] == [0, 3, 3, 9, 40]
    assert all(now == t for now, _, t in spy.calls)


def test_destination_dispatch_prefers_the_car_already_there():
    cfg = SimConfig(num_floors=30, num_elevators=2, start_floors=[1, 20])
    res = simulate(cfg, make_scheduler("destination_dispatch"), [R(0, "a", 20, 25)])
    assert by_id(res)["a"].elevator == 1
    assert by_id(res)["a"].wait_time == 0


def test_projection_does_not_mutate_the_car():
    car = Elevator(0, floor=3, capacity=4)
    car.assign(Passenger("a", 0, 5, 9))
    snapshot = (car.floor, car.direction, [p.id for p in car.pending], car.onboard[:])
    done = car.project(0, 0, 100, extra=Passenger("b", 0, 2, 7))
    assert {p.id for p in done} == {"a", "b"}
    assert (car.floor, car.direction, [p.id for p in car.pending], car.onboard) == snapshot
    assert car.pending[0].pickup_time is None


def test_deterministic():
    cfg = SimConfig(num_floors=30, num_elevators=3)
    reqs = generate("lunch", num_floors=30, duration=200, rate=0.5, seed=11)
    a = simulate(cfg, make_scheduler("destination_dispatch"), reqs)
    b = simulate(cfg, make_scheduler("destination_dispatch"), reqs)
    assert a.positions == b.positions


def test_fairness_exponent_must_be_at_least_one():
    with pytest.raises(ValueError):
        DestinationDispatch(fairness=0.5)


# --------------------------------------------------------- express / banks


@pytest.mark.parametrize("scheduler", sorted(SCHEDULERS))
def test_express_cars_only_get_trips_they_serve(scheduler):
    served = express_layout(40, 4, express_cars=2, express_from=21)
    cfg = SimConfig(num_floors=40, num_elevators=4, served_floors=served)
    reqs = generate("lunch", num_floors=40, duration=200, rate=0.6, seed=5)
    res = simulate(cfg, make_scheduler(scheduler), reqs)
    assert all(p.dropoff_time is not None for p in res.passengers)
    for p in res.passengers:
        assert cfg.can_serve(p.elevator, p.source, p.dest)
    # Express cars never stop between the lobby and floor 21.
    express_stops = {p.source for p in res.passengers if p.elevator >= 2}
    express_stops |= {p.dest for p in res.passengers if p.elevator >= 2}
    assert express_stops <= {1} | set(range(21, 41))


def test_banked_layout_serves_everything():
    served = banked_layout(30, low_cars=1, high_cars=1, split=16)
    cfg = SimConfig(num_floors=30, num_elevators=3, served_floors=served)
    reqs = generate("interfloor", num_floors=30, duration=100, rate=0.5, seed=2)
    res = simulate(cfg, make_scheduler("destination_dispatch"), reqs)
    assert all(p.dropoff_time is not None for p in res.passengers)


def test_layout_without_coverage_is_rejected():
    low = frozenset(range(1, 11))
    high = frozenset([1, *range(11, 21)])
    with pytest.raises(ValueError, match="no elevator serves"):
        SimConfig(num_floors=20, num_elevators=2, served_floors=[low, high])


# -------------------------------------------------------------- validation


def test_same_floor_requests_are_skipped():
    res = simulate(one_car(), make_scheduler("destination_dispatch"), [R(0, "a", 3, 3), R(0, "b", 1, 2)])
    assert [p.id for p in res.passengers] == ["b"]
    assert res.skipped and "a" in res.skipped[0]


@pytest.mark.parametrize(
    "reqs",
    [
        [R(0, "a", 0, 5)],  # below the lobby
        [R(0, "a", 1, 99)],  # above the roof
        [R(-1, "a", 1, 2)],  # negative time
        [R(0, "a", 1, 2), R(1, "a", 2, 3)],  # duplicate id
    ],
)
def test_bad_requests_raise(reqs):
    with pytest.raises(ValueError):
        simulate(one_car(), make_scheduler("destination_dispatch"), reqs)


def test_config_validation():
    with pytest.raises(ValueError):
        SimConfig(num_floors=1)
    with pytest.raises(ValueError):
        SimConfig(capacity=0)
    with pytest.raises(ValueError):
        SimConfig(num_elevators=2, start_floors=[1])


def test_lowest_floor_can_be_zero():
    cfg = SimConfig(num_floors=10, num_elevators=1, lowest_floor=0)
    res = simulate(cfg, make_scheduler("nearest_car"), [R(0, "a", 0, 9)])
    assert by_id(res)["a"].total_time == 9


def test_random_small_buildings_always_finish():
    rng = random.Random(0)
    for _ in range(25):
        floors = rng.randint(2, 15)
        cars = rng.randint(1, 4)
        cfg = SimConfig(
            num_floors=floors,
            num_elevators=cars,
            capacity=rng.randint(1, 4),
            stop_time=rng.randint(0, 2),
        )
        reqs = []
        for i in range(rng.randint(1, 60)):
            s, d = rng.sample(range(1, floors + 1), 2)
            reqs.append(R(rng.randint(0, 80), f"p{i}", s, d))
        for name in SCHEDULERS:
            res = simulate(cfg, make_scheduler(name), reqs)
            assert all(p.dropoff_time is not None for p in res.passengers)
