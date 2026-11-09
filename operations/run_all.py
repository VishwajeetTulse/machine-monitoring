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
from gaps import fill_gaps, coverage
from cycle_drift import drift
from mtbf_mttr import mtbf_mttr
from maintenance import cutting_hours, maintenance_due
from quality import load_rejects
from store import save_tables, add_log, query

DB = "output/results.db"


def run(settings_path, data_path, rejects_path=None):

    started = time.time()
    s = load_settings(settings_path)

    df = pd.read_csv(data_path, low_memory=False)
    df["state"] = classify_frame(df)

    # op11: mark missing readings, then op2: events
    events = mark_setup(
        to_events(fill_gaps(df, s["gap_seconds"])),
        setup_min_seconds=s["setup_min_seconds"],
    )

    # op3: parts come from the raw readings
    parts = to_parts(df)

    rejects = load_rejects(rejects_path) if rejects_path else None
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
    shift_table = pd.DataFrame(blocks)
    for col in ["top_reasons", "longest_stop", "notes"]:
        shift_table[col] = shift_table[col].apply(json.dumps)

    # op12
    d = s["drift"]
    drift_table = drift(parts, d["window"], d["watch_pct"], d["act_pct"])

    # op14
    hours = cutting_hours(df, s["timezone"])
    maint_table = maintenance_due(hours, s["maintenance_jobs"], s["last_done"])

    # op13
    mtbf_table = mtbf_mttr(events, s["failure_alarm_codes"]).rename_axis("machine").reset_index()

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

    # save only after everything worked, so an error never leaves half a database
    save_tables(DB, tables)

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
    a = p.parse_args()

    tables = run(a.settings, a.data, a.rejects)

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