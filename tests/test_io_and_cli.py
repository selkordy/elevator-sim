import csv
import io
import json
from pathlib import Path

import pytest

from elevator_sim import SimConfig, make_scheduler, simulate
from elevator_sim.cli import main
from elevator_sim.io_utils import read_requests, write_positions
from elevator_sim.schedulers import Elevator, Passenger, eta_to_pickup
from elevator_sim.models import Direction
from elevator_sim.stats import percentile, summarize
from elevator_sim.traffic import generate, parse_floor_spec

EXAMPLE = Path(__file__).resolve().parent.parent / "data" / "example.csv"


def test_read_requests_tolerates_spaces_and_blank_lines():
    text = "time, id, source, dest\n0, a, 1, 5\n\n3,b,5,1\n"
    reqs = read_requests(io.StringIO(text))
    assert [(r.time, r.id, r.source, r.dest) for r in reqs] == [(0, "a", 1, 5), (3, "b", 5, 1)]


def test_read_requests_reports_bad_rows():
    with pytest.raises(ValueError, match="line 2"):
        read_requests(io.StringIO("time,id,source,dest\nx,a,1,5\n"))
    with pytest.raises(ValueError, match="missing columns"):
        read_requests(io.StringIO("time,id,source\n0,a,1\n"))


def test_positions_file_has_one_row_per_tick(tmp_path):
    reqs = read_requests(EXAMPLE)
    res = simulate(SimConfig(num_floors=51, num_elevators=3), make_scheduler("nearest_car"), reqs)
    path = tmp_path / "positions.csv"
    write_positions(res, path)
    rows = list(csv.reader(open(path)))
    assert rows[0] == ["time", "elevator_0", "elevator_1", "elevator_2"]
    assert [int(r[0]) for r in rows[1:]] == list(range(res.ticks))


def test_cli_run_writes_outputs(tmp_path, capsys):
    assert main(["run", str(EXAMPLE), "--elevators", "2", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "wait time" in out and "total time" in out
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["delivered"] == 3
    assert (tmp_path / "positions.csv").exists() and (tmp_path / "passengers.csv").exists()


def test_cli_infers_floors_from_input(tmp_path, capsys):
    main(["run", str(EXAMPLE), "--out", str(tmp_path)])
    assert "floors 1-51" in capsys.readouterr().out


def test_cli_express_and_serve_flags(tmp_path, capsys):
    reqs = tmp_path / "reqs.csv"
    main(["generate", "--pattern", "lunch", "--floors", "30", "--duration", "100", "--seed", "1", "-o", str(reqs)])
    code = main(
        [
            "run", str(reqs), "--floors", "30", "--elevators", "3",
            "--express", "1", "--express-from", "16", "--out", str(tmp_path / "o"),
        ]
    )
    assert code == 0
    summary = json.loads((tmp_path / "o" / "summary.json").read_text())
    assert summary["delivered"] == summary["passengers"]


def test_generate_is_seeded():
    a = generate("up_peak", num_floors=20, duration=100, rate=0.5, seed=9)
    b = generate("up_peak", num_floors=20, duration=100, rate=0.5, seed=9)
    assert a == b
    assert all(1 <= r.source <= 20 and 1 <= r.dest <= 20 and r.source != r.dest for r in a)
    lobby_share = sum(r.source == 1 for r in a) / len(a)
    assert lobby_share > 0.6  # up-peak is mostly lobby departures


def test_parse_floor_spec():
    assert parse_floor_spec("1, 5-7,10") == frozenset({1, 5, 6, 7, 10})


def test_percentile():
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([7], 95) == 7


def test_eta_to_pickup():
    car = Elevator(0, floor=5, capacity=8)
    assert eta_to_pickup(car, Passenger("a", 0, 9, 12)) == 4  # idle
    car.assign(Passenger("x", 0, 10, 15))
    car.direction = Direction.UP
    assert eta_to_pickup(car, Passenger("a", 0, 8, 12)) == 3  # ahead, same way
    assert eta_to_pickup(car, Passenger("b", 0, 8, 2)) == (15 - 5) + (15 - 8)  # ahead, other way
    assert eta_to_pickup(car, Passenger("c", 0, 3, 1)) == (15 - 5) + (15 - 3)  # behind


def test_summary_counts_everyone():
    reqs = generate("lunch", num_floors=20, duration=100, rate=0.4, seed=4)
    res = simulate(SimConfig(num_floors=20, num_elevators=2), make_scheduler("zone"), reqs)
    s = summarize(res)
    assert s["delivered"] == len(reqs)
    assert s["wait"]["min"] >= 0
    assert s["total"]["mean"] == pytest.approx(s["wait"]["mean"] + s["travel"]["mean"])
    assert sum(c["riders"] for c in s["elevators"]) == len(reqs)
