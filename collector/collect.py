# the collector: reads every machine about once a second and saves the readings
# every machine is asked on its own, so a silent one never holds up the others

import argparse
import json
import queue
import sqlite3
import subprocess
import threading
import time

from readers import FakeReader, FocasReader, ReaderError
from readings import (
    blank_unsupported, check_capabilities, open_db, register_machines,
    unsupported_columns, write, write_heartbeat,
)

HEARTBEAT_SECONDS = 5

# with no network_check address: if nothing has answered for this long,
# the shop network is more likely at fault than every machine at once
ALL_SILENT_SECONDS = 15


def load_file(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_machines(path):
    machines = load_file(path)["machines"]

    codes = [m.get("code") for m in machines]
    if not machines or not all(codes):
        raise SystemExit("machines file: every machine needs a code")
    if len(set(codes)) != len(codes):
        raise SystemExit("machines file: a machine code is used twice")
    for m in machines:
        try:
            check_capabilities(m["code"], m.get("capabilities", {}))
        except ValueError as e:
            raise SystemExit(f"machines file: {e}")
    return machines


def make_readers(machines, kind, csv_path, outages):
    readers = {}
    for m in machines:
        if kind == "fake":
            readers[m["code"]] = FakeReader(m, csv_path, outages.get(m["code"]))
        else:
            if not m.get("ip"):
                raise SystemExit(f"machine {m['code']} needs an ip for the focas reader")
            readers[m["code"]] = FocasReader(m)
    return readers


def parse_outages(specs):
    # "V60,20,40" -> machine V60 stops answering from second 20, for 40 seconds
    out = {}
    for spec in specs:
        try:
            code, start, length = spec.split(",")
            out[code.strip()] = (float(start), float(length))
        except ValueError:
            raise SystemExit("--outage needs: MACHINE,START_SECOND,SECONDS   example: --outage V60,20,40")
    return out


class Poller(threading.Thread):
    # asks one machine, again and again. Its FOCAS connection is made and used here only.

    def __init__(self, code, reader, blank, out, answered, stop, interval, retry, times):
        super().__init__(daemon=True, name=f"poll-{code}")
        self.code = code
        self.reader = reader
        self.blank = blank
        self.out = out
        self.answered = answered
        self.stop_event = stop
        self.interval = interval
        self.retry = retry
        self.times = times

    def run(self):
        wait_until = 0
        failed = 0
        count = 0
        try:
            while not self.stop_event.is_set():
                began = time.time()

                if began >= wait_until:
                    try:
                        self.out.put(blank_unsupported(self.reader.read(), self.blank))
                        self.answered[self.code] = time.time()
                        if failed:
                            print(f"{self.code} is answering again")
                        failed = 0
                    except Exception as e:
                        # whatever went wrong, it stays with this machine
                        failed += 1
                        wait_until = began + self.retry
                        if failed == 1:
                            print(f"{self.code} not answering: {e}")
                        self.reader.close()

                count += 1
                if self.times and count >= self.times:
                    break
                self.stop_event.wait(max(0, self.interval - (time.time() - began)))
        finally:
            self.reader.close()


class NetworkWatch(threading.Thread):
    # pings one address that is always on, like the shop's switch or router

    def __init__(self, host, stop):
        super().__init__(daemon=True, name="network-watch")
        self.host = host
        self.stop_event = stop
        self.ok = True

    def ping(self):
        command = ["ping", "-n", "1", "-w", "1000", self.host]
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return False
        # Windows can return 0 for "destination unreachable", so look for a real reply
        return "ttl=" in done.stdout.lower()

    def run(self):
        while not self.stop_event.is_set():
            self.ok = self.ping()
            self.stop_event.wait(HEARTBEAT_SECONDS)


def network_ok(watch, answered, now):
    if watch is not None:
        return watch.ok
    return any(now - t < ALL_SILENT_SECONDS for t in list(answered.values()))


def drain(out, wait):
    rows = []
    try:
        rows.append(out.get(timeout=wait) if wait else out.get_nowait())
        while True:
            rows.append(out.get_nowait())
    except queue.Empty:
        pass
    return rows


def collect(readers, con, interval=1, retry=10, times=0, blank=None, network_check=None, stop=None):
    # one thread per machine, and this thread alone writes to the database

    blank = blank or {}
    stop = stop or threading.Event()
    out = queue.Queue()
    answered = {code: 0.0 for code in readers}

    pollers = [
        Poller(code, reader, blank.get(code, []), out, answered, stop, interval, retry, times)
        for code, reader in readers.items()
    ]
    for p in pollers:
        p.start()

    watch = None
    if network_check:
        watch = NetworkWatch(network_check, stop)
        watch.start()

    pending = []
    state = {"saved": 0, "next_beat": 0.0, "last_print": time.time(), "db_failing": False}

    def loop():
        while True:
            alive = any(p.is_alive() for p in pollers)
            pending.extend(drain(out, 0.2 if alive else 0))

            try:
                if pending:
                    write(con, pending)
                    state["saved"] += len(pending)
                    pending.clear()

                now = time.time()
                if now >= state["next_beat"]:
                    write_heartbeat(con, network_ok(watch, answered, now))
                    state["next_beat"] = now + HEARTBEAT_SECONDS
                state["db_failing"] = False
            except sqlite3.OperationalError as e:
                # the rows are kept and tried again
                if not state["db_failing"]:
                    print(f"database busy, will retry: {e}")
                state["db_failing"] = True

            if time.time() - state["last_print"] >= 30:
                print(f"{time.strftime('%H:%M:%S')}  saved {state['saved']} readings so far")
                state["last_print"] = time.time()

            if not alive and out.empty():
                return

    try:
        loop()
    except KeyboardInterrupt:
        print("stopped")
        stop.set()
        loop()

    stop.set()
    return state["saved"]


if __name__ == "__main__":

    p = argparse.ArgumentParser()
    p.add_argument("--machines", default="collector/machines.json")
    p.add_argument("--reader", choices=["fake", "focas"], default="fake")
    p.add_argument("--csv", default="data/cnc_sim_data.csv", help="fake reader only")
    p.add_argument("--db", default="output/live.db")
    p.add_argument("--interval", type=float, default=1, help="seconds between readings")
    p.add_argument("--retry", type=float, default=10, help="seconds to wait before asking a silent machine again")
    p.add_argument("--times", type=int, default=0, help="stop after this many rounds (0 = never)")
    p.add_argument("--fresh", action="store_true", help="delete the old readings and operator reasons first")
    p.add_argument("--outage", action="append", default=[], help="fake reader only: MACHINE,START_SECOND,SECONDS")
    a = p.parse_args()

    machines = load_machines(a.machines)
    try:
        readers = make_readers(machines, a.reader, a.csv, parse_outages(a.outage))
    except ReaderError as e:
        raise SystemExit(f"cannot start: {e}")
    con = open_db(a.db)
    register_machines(con, machines)

    if a.fresh:
        con.execute("DELETE FROM readings")
        con.execute("DELETE FROM heartbeat")
        con.execute("DROP TABLE IF EXISTS reasons")
        con.commit()
        print("old readings deleted")

    print(f"collecting {len(machines)} machines every {a.interval:g} s with the {a.reader} reader. Ctrl+C to stop.")

    blank = {m["code"]: unsupported_columns(m.get("capabilities", {})) for m in machines}
    try:
        collect(readers, con, a.interval, a.retry, a.times, blank,
                load_file(a.machines).get("network_check"))
    finally:
        con.close()
