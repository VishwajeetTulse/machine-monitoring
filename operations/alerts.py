# looks at the latest results and opens or clears alerts

import argparse
import sqlite3

import pandas as pd

from settings import load_settings
from shift_calendar import is_working
from store import query, live_now

RESULTS_DB = "output/results.db"
LIVE_DB = "output/live.db"


def clock(text, zone):
    # "2026-11-02T04:30:00Z" -> "10:00"
    return pd.to_datetime(text, utc=True).tz_convert(zone).strftime("%H:%M")


def latest_events(results_db):
    e = query(results_db, "SELECT * FROM events")
    e = e.sort_values(["machine", "started_at"])
    # the newest event of each machine = what it is doing now
    return e.groupby("machine").tail(1)


def last_readings(live_db):
    # newest reading of each machine
    last = query(live_db, "SELECT machine_code, MAX(timestamp) AS last FROM readings GROUP BY machine_code")
    last["last"] = pd.to_datetime(last["last"], utc=True)
    return last


def conditions(settings, results_db, live_db, now=None, silent_only=False):
    # silent_only: there are no results for today, so only silence can be judged

    a = settings["alerts"]
    zone = settings["timezone"]
    found = []

    now = now if now is not None else live_now(live_db)
    if now is None:
        return None, found
    last = last_readings(live_db)

    # machine silent. Outside working time a silent machine is normal.
    if is_working(now, settings):
        for _, r in last.iterrows():
            minutes = (now - r["last"]).total_seconds() / 60
            if minutes >= a["silent_minutes"]:
                stamp = r["last"].strftime("%Y-%m-%dT%H:%M:%SZ")
                found.append({
                    "key": f"silent|{r['machine_code']}|{stamp}",
                    "rule": "silent", "machine": r["machine_code"], "since": stamp,
                    "message": f"{r['machine_code']} sent no readings for {minutes:.0f} min, last reading {clock(stamp, zone)}",
                })

    if silent_only:
        return now, found

    # alarm too long, idle too long
    for _, e in latest_events(results_db).iterrows():
        minutes = e["duration_s"] / 60
        since = clock(e["started_at"], zone)

        if e["state"] == "ALARM" and minutes >= a["alarm_minutes"]:
            code = int(e["alarm_code"]) if pd.notna(e["alarm_code"]) else "unknown"
            found.append({
                "key": f"alarm|{e['machine']}|{e['started_at']}",
                "rule": "alarm", "machine": e["machine"], "since": e["started_at"],
                "message": f"{e['machine']} in ALARM for {minutes:.0f} min (code {code}), since {since}",
            })

        if e["state"] == "IDLE" and minutes >= a["idle_minutes"]:
            reason = e["reason"] if pd.notna(e["reason"]) else "not given"
            found.append({
                "key": f"idle|{e['machine']}|{e['started_at']}",
                "rule": "idle", "machine": e["machine"], "since": e["started_at"],
                "message": f"{e['machine']} idle for {minutes:.0f} min, reason {reason}, since {since}",
            })

    # drift: the current program run of each machine
    d = query(results_db, "SELECT * FROM drift").sort_values(["machine", "started_at"])
    for _, r in d.groupby("machine").tail(1).iterrows():
        if r["status"] in a["drift_levels"]:
            found.append({
                "key": f"drift|{r['machine']}|{r['started_at']}|{r['status']}",
                "rule": "drift", "machine": r["machine"], "since": r["started_at"],
                "message": f"{r['machine']} program {int(r['program'])}: last {settings['drift']['window']} parts are {r['now_pct']}% slower than ideal ({r['status']})",
            })

    # maintenance
    m = query(results_db, "SELECT * FROM maintenance")
    for _, r in m[m["status"].isin(a["maintenance_levels"])].iterrows():
        if r["status"] == "OVERDUE":
            text = f"{abs(r['left_h']):.1f} h overdue"
        elif r["status"] == "DUE SOON":
            text = f"due in {r['left_h']:.1f} h"
        else:
            text = "no service record"
        found.append({
            "key": f"maintenance|{r['machine']}|{r['job']}|{r['status']}",
            "rule": "maintenance", "machine": r["machine"], "since": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "message": f"{r['machine']} {r['job']}: {text}",
        })

    return now, found


def open_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            key TEXT PRIMARY KEY, rule TEXT, machine TEXT, message TEXT,
            since TEXT, opened_at TEXT, cleared_at TEXT, status TEXT
        )""")


def sync(results_db, now, found):
    # compares what is wrong now with what was already open

    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    opened, cleared = [], []

    con = sqlite3.connect(results_db)
    try:
        open_table(con)
        known = dict(con.execute("SELECT key, status FROM alerts").fetchall())
        now_keys = {c["key"] for c in found}

        for c in found:
            state = known.get(c["key"])
            if state is None:
                con.execute(
                    "INSERT INTO alerts VALUES (?,?,?,?,?,?,NULL,'OPEN')",
                    (c["key"], c["rule"], c["machine"], c["message"], c["since"], stamp),
                )
                opened.append(c)
            elif state == "CLEARED":
                # same problem came back
                con.execute("UPDATE alerts SET status='OPEN', cleared_at=NULL, message=? WHERE key=?",
                            (c["message"], c["key"]))
                opened.append(c)

        for key, rule, machine, message in con.execute(
            "SELECT key, rule, machine, message FROM alerts WHERE status='OPEN'"
        ).fetchall():
            if key not in now_keys:
                cleared.append({"key": key, "rule": rule, "machine": machine, "message": message})

        for c in cleared:
            con.execute("UPDATE alerts SET status='CLEARED', cleared_at=? WHERE key=?", (stamp, c["key"]))

        con.commit()
    finally:
        con.close()

    return opened, cleared


def check(settings, results_db=RESULTS_DB, live_db=LIVE_DB, now=None, silent_only=False):
    now, found = conditions(settings, results_db, live_db, now, silent_only)
    if now is None:
        return [], []
    return sync(results_db, now, found)


def reset(results_db=RESULTS_DB):
    con = sqlite3.connect(results_db)
    try:
        con.execute("DROP TABLE IF EXISTS alerts")
        con.commit()
    finally:
        con.close()


if __name__ == "__main__":

    p = argparse.ArgumentParser()
    p.add_argument("--settings", default="settings/example_client.json")
    p.add_argument("--reset", action="store_true", help="delete all alerts")
    a = p.parse_args()

    if a.reset:
        reset()
        print("alerts deleted")
    else:
        s = load_settings(a.settings)
        opened, cleared = check(s)
        print(f"opened now: {len(opened)}   cleared now: {len(cleared)}")
        print()
        try:
            print(query(RESULTS_DB, "SELECT status, rule, machine, message FROM alerts ORDER BY status DESC, rule, machine").to_string(index=False))
        except Exception:
            print("no alerts yet. Run replay.py and auto_run.py first.")