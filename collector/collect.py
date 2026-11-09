# the collector: reads every machine about once a second and saves the readings
# a machine that does not answer is skipped for a while, the others carry on

import argparse
import json
import time

from readers import FakeReader, FocasReader, ReaderError
from readings import open_db, write


def load_machines(path):
    with open(path, encoding="utf-8") as f:
        machines = json.load(f)["machines"]

    codes = [m.get("code") for m in machines]
    if not machines or not all(codes):
        raise SystemExit("machines file: every machine needs a code")
    if len(set(codes)) != len(codes):
        raise SystemExit("machines file: a machine code is used twice")
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

    if a.fresh:
        con.execute("DELETE FROM readings")
        con.execute("DROP TABLE IF EXISTS reasons")
        con.commit()
        print("old readings deleted")

    print(f"collecting {len(machines)} machines every {a.interval:g} s with the {a.reader} reader. Ctrl+C to stop.")

    wait_until = {}     # machine -> time we may ask again
    failed = {}         # machine -> failed rounds in a row
    saved = 0
    count = 0
    last_print = time.time()

    try:
        while True:
            began = time.time()
            rows = []

            for code, reader in readers.items():
                if began < wait_until.get(code, 0):
                    continue
                try:
                    rows.append(reader.read())
                    if failed.get(code):
                        print(f"{code} is answering again")
                        failed[code] = 0
                except ReaderError as e:
                    failed[code] = failed.get(code, 0) + 1
                    wait_until[code] = began + a.retry
                    if failed[code] == 1:
                        print(f"{code} not answering: {e}")
                    reader.close()

            write(con, rows)
            saved += len(rows)

            if time.time() - last_print >= 30:
                print(f"{time.strftime('%H:%M:%S')}  saved {saved} readings so far")
                last_print = time.time()

            count += 1
            if a.times and count >= a.times:
                break
            time.sleep(max(0, a.interval - (time.time() - began)))
    except KeyboardInterrupt:
        print("stopped")
    finally:
        for r in readers.values():
            r.close()
        con.close()