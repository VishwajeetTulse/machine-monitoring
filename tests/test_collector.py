# the collector must keep healthy machines going whatever the others do

import time

from collect import ALL_SILENT_SECONDS, collect, load_machines, network_ok
from readers import ReaderError
from readings import open_db, register_machines
import pytest


class Healthy:
    def __init__(self, code):
        self.code = code

    def read(self):
        return {"machine_code": self.code, "timestamp": str(time.time()), "execution": "ACTIVE",
                "alarm_code": 0, "parts_today": 3}

    def close(self):
        pass


class SlowToFail:
    # like a powered-off machine: the connect waits, then fails
    def __init__(self, seconds):
        self.seconds = seconds
        self.tries = 0

    def read(self):
        self.tries += 1
        time.sleep(self.seconds)
        raise ReaderError("no answer")

    def close(self):
        pass


class Exploding:
    def read(self):
        raise OSError("access violation")

    def close(self):
        pass


def rows(con, code):
    return con.execute("SELECT COUNT(*) FROM readings WHERE machine_code = ?", (code,)).fetchone()[0]


@pytest.fixture
def con(tmp_path):
    connection = open_db(tmp_path / "live.db")
    yield connection
    connection.close()


def test_every_machine_is_read_every_round(con):
    collect({"A": Healthy("A"), "B": Healthy("B")}, con, interval=0.01, times=5)
    assert rows(con, "A") == 5
    assert rows(con, "B") == 5


def test_a_slow_silent_machine_does_not_hold_up_a_healthy_one(con):
    # polled one after another this would give 2 readings in 0.8 s. On its own thread, far more.
    readers = {"SILENT": SlowToFail(0.4), "A": Healthy("A")}
    collect(readers, con, interval=0.02, retry=0.01, times=30)
    assert rows(con, "A") == 30
    assert rows(con, "SILENT") == 0


def test_two_silent_machines_cannot_create_a_gap_on_a_healthy_one(con):
    readers = {"S1": SlowToFail(0.3), "S2": SlowToFail(0.3), "A": Healthy("A")}
    began = time.time()
    collect(readers, con, interval=0.02, retry=0.01, times=10)
    stamps = sorted(float(r[0]) for r in con.execute("SELECT timestamp FROM readings WHERE machine_code = 'A'"))
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert stamps and max(gaps) < 0.25, f"longest wait {max(gaps):.2f}s in a {time.time() - began:.1f}s run"


def test_a_crash_on_one_machine_stays_on_that_machine(con):
    collect({"BAD": Exploding(), "A": Healthy("A")}, con, interval=0.01, retry=0.01, times=5)
    assert rows(con, "A") == 5


def test_a_silent_machine_is_only_asked_again_after_the_retry(con):
    silent = SlowToFail(0)
    collect({"S": silent}, con, interval=0.01, retry=60, times=5)
    assert silent.tries == 1


def test_unsupported_columns_are_saved_empty(con):
    collect({"A": Healthy("A")}, con, interval=0.01, times=1, blank={"A": ["parts_today"]})
    assert con.execute("SELECT parts_today FROM readings").fetchone()[0] is None


def test_the_collector_records_a_heartbeat(con):
    collect({"A": Healthy("A")}, con, interval=0.01, times=3)
    assert con.execute("SELECT COUNT(*) FROM heartbeat").fetchone()[0] >= 1


def test_the_shop_is_on_the_clock_of_the_real_world(con):
    assert con.execute("SELECT value FROM meta WHERE key = 'clock'").fetchone()[0] == "real"


def test_machines_are_registered(con):
    register_machines(con, [{"code": "A", "capabilities": {"part_count": False}}, {"code": "B"}])
    assert [r[0] for r in con.execute("SELECT code FROM machines ORDER BY code")] == ["A", "B"]


def test_network_is_ok_while_any_machine_answers():
    now = 1000.0
    assert network_ok(None, {"A": now - 1, "B": 0.0}, now) is True


def test_network_is_doubtful_when_everything_is_silent():
    now = 1000.0
    assert network_ok(None, {"A": now - ALL_SILENT_SECONDS - 1, "B": 0.0}, now) is False


def test_a_network_check_address_decides_when_given():
    class Watch:
        ok = False
    assert network_ok(Watch(), {"A": 999.0}, 1000.0) is False


def test_a_wrong_capability_in_the_file_stops_the_start(tmp_path):
    path = tmp_path / "machines.json"
    path.write_text('{"machines": [{"code": "A", "capabilities": {"parts": true}}]}')
    with pytest.raises(SystemExit):
        load_machines(path)
