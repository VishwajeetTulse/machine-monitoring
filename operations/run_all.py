# runs every operation for one client and saves the results

import argparse
import json
import time
from datetime import datetime, timezone

import pandas as pd

from settings import load_settings
from test_classification import classify_frame
from state_events import to_events, mark_setup
from part_counting import to_parts
from downtime import downtime_by_reason
from oee import oee
from shift_summary import summarise
from gaps import fill_gaps, coverage, healthy_windows
from cycle_drift import drift
from mtbf_mttr import mtbf_totals, mtbf_from_totals
from maintenance import cutting_hours, hours_from_rollup, maintenance_due
from quality import load_rejects
import reasons as stop_reasons
import shift_report
from shift_calendar import apply_calendar, working_day_start
from store import (
    save_tables, add_log, query, live_now, read_live, read_heartbeat, read_machines,
    get_state, set_state, load_rollup, save_rollup,
)

DB = "output/results.db"
LIVE_DB = "output/live.db"

# MTBF and the maintenance rate need more than one day
HISTORY_DAYS = 30

SHIFT_COLUMNS = [
    "machine", "shift", "date", "window", "parts", "run_min", "planned_min", "availability_pct",
    "performance_pct", "quality_pct", "oee_pct", "stops", "downtime_min", "top_reasons",
    "longest_stop", "notes",
]


class NoReadings(ValueError):
    pass


def work_date(day_start, zone):
    return day_start.tz_convert(zone).strftime("%Y-%m-%d")


def live_window(start, end):
    return {
        "start": start,
        "end": end,
        "machines": read_machines(LIVE_DB),
        "healthy": healthy_windows(read_heartbeat(LIVE_DB, start, end + pd.Timedelta(seconds=1))),
    }


def daily_row(day, totals, df):
    # today's totals, in the shape they are kept across days
    d = df[["machine_code", "timestamp"]].copy()
    d["cutting_min"] = pd.to_numeric(df["cutting_min"], errors="coerce") if "cutting_min" in df else float("nan")
    known = d.dropna(subset=["cutting_min"]).sort_values("timestamp").groupby("machine_code")["cutting_min"]

    rows = totals.copy()
    rows["machine"] = rows.index
    rows["work_date"] = day
    rows["cut_first_min"] = known.first().reindex(rows.index)
    rows["cut_last_min"] = known.last().reindex(rows.index)
    return rows.reset_index(drop=True)


def calculate(df, s, rejects=None, window=None, day=None):
    # every operation, on the readings given. Nothing is saved here.

    df = df.copy()
    df["state"] = classify_frame(df)

    # op11: mark missing readings, then op2: events
    events = mark_setup(
        to_events(fill_gaps(df, s["gap_seconds"], **(window or {}))),
        setup_min_seconds=s["setup_min_seconds"],
    )

    # breaks and time outside the shifts are not a loss
    events = apply_calendar(events, s)

    # reasons tapped by the operator replace the reason in the data (live only)
    if window:
        events = stop_reasons.apply_reasons(events, stop_reasons.load(LIVE_DB))

    # op3: parts come from the raw readings
    parts = to_parts(df)

    planned = s["setup_is_planned"]

    # op4: stops by reason, one machine at a time
    downtime = pd.concat(
        {m: downtime_by_reason(g) for m, g in events.groupby("machine")},
        names=["machine", "reason"],
    ).reset_index()

    # ops 5, 6, 7, 8
    oee_table = oee(events, parts, planned, rejects).rename_axis("machine").reset_index()

    # op9: JSON parts are saved as text
    blocks = summarise(events, parts, s["shifts"], planned, s["timezone"])
    # readings that touch no shift give no blocks, and that is not an error
    shift_table = pd.DataFrame(blocks, columns=SHIFT_COLUMNS)
    for col in ["top_reasons", "longest_stop", "notes"]:
        shift_table[col] = shift_table[col].apply(json.dumps)

    # op12
    d = s["drift"]
    drift_table = drift(parts, d["window"], d["watch_pct"], d["act_pct"])

    # ops 13 and 14: these need more than one day when the data is live
    totals = mtbf_totals(events, s["failure_alarm_codes"])
    today = None
    if window:
        today = daily_row(day, totals, df)
        history = load_rollup(DB, day, HISTORY_DAYS)
        rollup = today if history.empty else pd.concat([history, today], ignore_index=True)
        added = rollup.groupby("machine")[["failures", "run_s", "alarm_s"]].sum()
        mtbf_table = mtbf_from_totals(added).rename_axis("machine").reset_index()
        hours = hours_from_rollup(rollup)
    else:
        mtbf_table = mtbf_from_totals(totals).rename_axis("machine").reset_index()
        hours = cutting_hours(df, s["timezone"])

    maint_table = maintenance_due(hours, s["maintenance_jobs"], s["last_done"])

    # op11
    cover_table = coverage(events).rename_axis("machine").reset_index()

    tables = {
        "events": events,
        "parts": parts,
        "downtime": downtime,
        "oee": oee_table,
        "shift_summary": shift_table,
        "drift": drift_table,
        "maintenance": maint_table,
        "mtbf_mttr": mtbf_table,
        "coverage": cover_table,
    }
    return tables, today


def close_previous_day(s, day_start, rejects):
    # the last shift of a day ends as the next day begins, so its report and
    # totals have to be finished before the new day replaces them

    stored = get_state(DB, "day_start")
    set_state(DB, "day_start", day_start.isoformat())
    if stored is None or pd.Timestamp(stored) >= day_start:
        return

    old_start = pd.Timestamp(stored)
    try:
        df = read_live(LIVE_DB, old_start, day_start)
        if df.empty:
            return
        window = live_window(old_start, day_start - pd.Timedelta(seconds=1))
        tables, totals = calculate(df, s, rejects, window, work_date(old_start, s["timezone"]))
        save_tables(DB, tables)
        save_rollup(DB, totals)
        shift_report.check(s, DB, now=day_start)
    except Exception as e:
        # one bad day must not stop today from being worked out
        print(f"could not close the day that began {old_start}: {type(e).__name__}: {e}")


def run(settings_path, data_path, rejects_path=None, source="csv"):

    started = time.time()
    s = load_settings(settings_path)
    rejects = load_rejects(rejects_path) if rejects_path else None

    # readings come from the CSV file, or from the live database
    if source == "live":
        now = live_now(LIVE_DB)
        if now is None:
            raise NoReadings("no readings in the live database yet")

        # only the current working day. The readings stay in the database.
        day_start = working_day_start(now, s)
        close_previous_day(s, day_start, rejects)

        df = read_live(LIVE_DB, day_start)
        if df.empty:
            raise NoReadings(f"no readings since {day_start.tz_convert(s['timezone']):%d %b %H:%M}")

        window = live_window(day_start, now)
        day = work_date(day_start, s["timezone"])
        data_path = LIVE_DB
    else:
        df = pd.read_csv(data_path, low_memory=False)
        window = None
        day = None

    tables, today = calculate(df, s, rejects, window, day)

    # save only after everything worked, so an error never leaves half a database
    save_tables(DB, tables)
    if today is not None:
        save_rollup(DB, today)

    add_log(DB, {
        "run_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "client": s["client"],
        "settings_file": settings_path,
        "data_file": data_path,
        "readings": len(df),
        "machines": df["machine_code"].nunique(),
        "seconds_taken": round(time.time() - started, 1),
    })

    return tables


if __name__ == "__main__":

    p = argparse.ArgumentParser()
    p.add_argument("--settings", default="settings/example_client.json")
    p.add_argument("--data", default="data/cnc_sim_data.csv")
    p.add_argument("--rejects", default=None)
    p.add_argument("--source", choices=["csv", "live"], default="csv")
    a = p.parse_args()

    tables = run(a.settings, a.data, a.rejects, a.source)

    print("saved to", DB)
    print()
    print("rows saved:")
    for name, t in tables.items():
        print(f"  {name:<14}{len(t):>6}")
    print()

    # read back from the database, not from memory
    print("read back from the database:")
    print("  parts :", int(query(DB, "SELECT SUM(parts_added) AS n FROM parts")["n"][0]))
    print("  events:", int(query(DB, "SELECT COUNT(*) AS n FROM events")["n"][0]))
    print()
    print(query(DB, "SELECT machine, availability_pct, performance_pct, quality_pct, oee_pct, note FROM oee").to_string(index=False))
    print()
    print("runs so far:", int(query(DB, "SELECT COUNT(*) AS n FROM run_log")["n"][0]))