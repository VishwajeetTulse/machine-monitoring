# how often machines fail (MTBF) and how long a fix takes (MTTR)

import pandas as pd

from test_classification import classify_frame, RUNNING, ALARM
from state_events import to_events, mark_setup
from settings import load_settings


def mtbf_totals(events, failure_codes=()):

    # run time per machine
    run_s = events[events["state"] == RUNNING].groupby("machine")["duration_s"].sum()

    # one ALARM period = one failure
    is_alarm = events["state"] == ALARM

    # only the alarm codes the client counts as failures. Empty list = all.
    if len(failure_codes) > 0:
        if "alarm_code" not in events.columns:
            raise ValueError("events table has no alarm_code column")
        is_alarm = is_alarm & events["alarm_code"].isin(failure_codes)

    alarms = events[is_alarm].groupby("machine")["duration_s"].agg(["count", "sum"])

    machines = sorted(events["machine"].unique())

    table = pd.DataFrame(index=machines)
    table["failures"] = alarms["count"].reindex(machines).fillna(0).astype(int)
    table["run_s"] = run_s.reindex(machines).fillna(0)
    table["alarm_s"] = alarms["sum"].reindex(machines).fillna(0)

    return table


def mtbf_from_totals(table):

    table = table[["failures", "run_s", "alarm_s"]].copy()
    table["failures"] = table["failures"].astype(int)

    # add the seconds first, then divide
    table.loc["ALL"] = [table["failures"].sum(), table["run_s"].sum(), table["alarm_s"].sum()]
    table["failures"] = table["failures"].astype(int)

    # no failures -> blank, not zero and not infinite
    f = table["failures"].where(table["failures"] > 0)

    out = pd.DataFrame(index=table.index)
    out["failures"] = table["failures"]
    out["run_min"] = (table["run_s"] / 60).round(1)
    out["alarm_min"] = (table["alarm_s"] / 60).round(1)
    out["mtbf_min"] = (table["run_s"] / f / 60).round(1)
    out["mttr_min"] = (table["alarm_s"] / f / 60).round(1)
    out["note"] = ""
    out.loc[out["failures"] == 0, "note"] = "no failures"
    out.loc[out["failures"] == 1, "note"] = "only 1 failure"

    return out


def mtbf_mttr(events, failure_codes=()):
    return mtbf_from_totals(mtbf_totals(events, failure_codes))


if __name__ == "__main__":

    s = load_settings("settings/example_client.json")
    codes = s["failure_alarm_codes"]

    df = pd.read_csv("data/cnc_sim_data.csv", low_memory=False)

    # real file
    df["state"] = classify_frame(df)
    events = mark_setup(to_events(df), setup_min_seconds=s["setup_min_seconds"])

    print("real file:")
    print(mtbf_mttr(events, codes).to_string())
    print()

    # test copy with made-up alarms. Not saved.
    test = pd.read_csv("data/cnc_sim_data.csv", low_memory=False)
    t = pd.to_datetime(test["timestamp"])
    start = t.min()

    def alarm(machine, hours, minutes):
        begin = start + pd.Timedelta(hours=hours)
        end = begin + pd.Timedelta(minutes=minutes)
        test.loc[(test["machine_code"] == machine) & (t >= begin) & (t < end), "alarm_code"] = 1234

    alarm("V60", 1, 5)
    alarm("V60", 4, 15)
    alarm("V61", 2, 20)

    test["state"] = classify_frame(test)
    test_events = mark_setup(to_events(test), setup_min_seconds=s["setup_min_seconds"])

    print("test copy: V60 alarms 5 and 15 min, V61 one of 20 min:")
    print(mtbf_mttr(test_events, codes).to_string())