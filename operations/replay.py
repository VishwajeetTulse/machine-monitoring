# plays the simulator CSV into a database one second at a time

import argparse
import sqlite3
import time
from pathlib import Path

import pandas as pd

# columns that only the simulator knows. A real machine never sends these.
HIDDEN = ["state"]


def parse_fault(spec, name):
    # "V60,10:00,8" -> machine, start (IST clock), minutes
    try:
        machine, start, minutes = spec.split(",")
        hours, mins = start.split(":")
        return machine.strip(), int(hours), int(mins), float(minutes)
    except ValueError:
        raise SystemExit(f"--{name} needs: MACHINE,HH:MM,MINUTES   example: --{name} V60,10:00,8")


def apply_faults(df, alarms, silents, zone="Asia/Kolkata"):
    # test faults, so alerts can be tried. They change the data on purpose.
    day = df["_t"].iloc[0].tz_convert(zone).normalize()

    def window(spec, name):
        machine, hours, mins, length = parse_fault(spec, name)
        if machine not in set(df["machine_code"]):
            raise SystemExit(f"--{name}: no machine called {machine}")
        begin = (day + pd.Timedelta(hours=hours, minutes=mins)).tz_convert("UTC")
        mask = (df["machine_code"] == machine) & (df["_t"] >= begin) & (df["_t"] < begin + pd.Timedelta(minutes=length))
        return mask

    for spec in alarms:
        mask = window(spec, "alarm")
        df.loc[mask, "alarm_code"] = 1234
        df.loc[mask, "execution"] = "STOPPED"

    for spec in silents:
        # these readings are never sent
        df = df[~window(spec, "silent")]

    return df.reset_index(drop=True)


def load_csv(path, alarms=(), silents=()):
    df = pd.read_csv(path, low_memory=False)

    # drop the simulator's answers, so the system must work them out
    drop = [c for c in df.columns if c in HIDDEN or c.startswith("_")]
    df = df.drop(columns=drop)

    df["_t"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("_t", kind="stable").reset_index(drop=True)
    return apply_faults(df, alarms, silents)


def create_table(con, df):
    # fixed column types, so the first small batch cannot confuse the table
    kinds = {"i": "INTEGER", "u": "INTEGER", "f": "REAL"}
    cols = ", ".join(f'"{c}" {kinds.get(df[c].dtype.kind, "TEXT")}' for c in df.columns if c != "_t")
    con.execute(f"CREATE TABLE IF NOT EXISTS readings ({cols})")
    con.commit()


def last_stored(con):
    try:
        row = con.execute("SELECT MAX(timestamp) FROM readings").fetchone()
    except sqlite3.OperationalError:
        return None
    return pd.to_datetime(row[0]) if row and row[0] else None


def replay(data_path, db_path, speed, keep=False, alarms=(), silents=()):

    df = load_csv(data_path, alarms, silents)

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)

    # readers and the writer can work at the same time
    con.execute("PRAGMA journal_mode=WAL")

    first = df["_t"].iloc[0]
    sent = 0

    if keep:
        # carry on after the last stored reading
        stop = last_stored(con)
        if stop is not None:
            sent = int((df["_t"] <= stop).sum())
            print(f"keeping old readings, carrying on after {stop}")
    else:
        con.execute("DROP TABLE IF EXISTS readings")
        con.commit()

    create_table(con, df)

    for spec in alarms:
        print(f"test fault: alarm {spec}")
    for spec in silents:
        print(f"test fault: silent {spec}")

    total = len(df)
    print(f"playing {total:,} readings at {speed}x. Ctrl+C to stop.")

    began = time.time()
    # simulated seconds already played when we start
    played = (df["_t"].iloc[sent - 1] - first).total_seconds() if sent else 0
    offset = played / speed

    while sent < total:

        # how much simulated time should have passed by now
        elapsed = time.time() - began + offset
        cutoff = first + pd.Timedelta(seconds=elapsed * speed)

        upto = int((df["_t"] <= cutoff).sum())
        if upto > sent:
            batch = df.iloc[sent:upto].drop(columns=["_t"])
            batch.to_sql("readings", con, if_exists="append", index=False)
            con.commit()
            sent = upto

            clock = df["_t"].iloc[sent - 1].tz_convert("Asia/Kolkata")
            print(f"  {clock:%H:%M:%S} IST   {sent:>7,} / {total:,} readings")

        time.sleep(1)

    con.execute("CREATE INDEX IF NOT EXISTS idx_readings ON readings (machine_code, timestamp)")
    con.commit()
    con.close()
    print("done")


if __name__ == "__main__":

    p = argparse.ArgumentParser()
    p.add_argument("--data", default="data/cnc_sim_data.csv")
    p.add_argument("--db", default="output/live.db")
    p.add_argument("--speed", type=float, default=600, help="simulated seconds per real second")
    p.add_argument("--keep", action="store_true", help="carry on after the stored readings")
    p.add_argument("--alarm", action="append", default=[], help="test fault MACHINE,HH:MM,MINUTES (IST)")
    p.add_argument("--silent", action="append", default=[], help="test fault MACHINE,HH:MM,MINUTES (IST)")
    a = p.parse_args()

    try:
        replay(a.data, a.db, a.speed, a.keep, a.alarm, a.silent)
    except KeyboardInterrupt:
        print("stopped")