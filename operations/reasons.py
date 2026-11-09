# stop reasons tapped by the operator, and how they reach the stops

import sqlite3
from datetime import datetime, timezone

import pandas as pd

LIVE_DB = "output/live.db"

# a reason can only be given to a stop
STOP_STATES = ["IDLE", "SETUP"]


def open_table(con):
    con.execute("CREATE TABLE IF NOT EXISTS reasons (machine TEXT, at TEXT, reason TEXT, entered_at TEXT)")


def add_reason(machine, at, reason, db=LIVE_DB):
    # at = time of the machine's newest reading, so it always falls inside the stop
    con = sqlite3.connect(db, timeout=30)
    try:
        open_table(con)
        con.execute(
            "INSERT INTO reasons VALUES (?, ?, ?, ?)",
            (machine, at, reason, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
        )
        con.commit()
    finally:
        con.close()


def load(db=LIVE_DB):
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
    except sqlite3.OperationalError:
        return pd.DataFrame(columns=["machine", "at", "reason", "entered_at"])
    try:
        return pd.read_sql_query("SELECT machine, at, reason, entered_at FROM reasons ORDER BY at, rowid", con)
    except Exception:
        # table not created yet
        return pd.DataFrame(columns=["machine", "at", "reason", "entered_at"])
    finally:
        con.close()


def apply_reasons(events, reasons):
    # the operator's reason replaces the one in the data, for the stop it falls in
    events = events.copy()
    if reasons.empty:
        return events

    reasons = reasons.copy()
    reasons["at"] = pd.to_datetime(reasons["at"], utc=True)

    # oldest first, so the newest tap on a stop wins
    for _, r in reasons.sort_values("at").iterrows():
        hit = (
            (events["machine"] == r["machine"])
            & events["state"].isin(STOP_STATES)
            & (events["started_at"] <= r["at"])
            & (events["ended_at"] >= r["at"])
        )
        events.loc[hit, "reason"] = r["reason"]

    return events


def reset(db=LIVE_DB):
    con = sqlite3.connect(db, timeout=30)
    try:
        con.execute("DROP TABLE IF EXISTS reasons")
        con.commit()
    finally:
        con.close()