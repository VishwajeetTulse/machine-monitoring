# tells which machine needs servicing based on cutting hours

import pandas as pd

from settings import load_settings

# due soon when this share of the interval is left
SOON_SHARE = 0.10


def cutting_hours(df, zone):

    d = df.copy()
    d["timestamp"] = pd.to_datetime(d["timestamp"])
    d = d.sort_values(["machine_code", "timestamp"])

    machines = sorted(d["machine_code"].unique())

    if "cutting_min" not in d.columns:
        d["cutting_min"] = float("nan")

    # working days on file in IST
    days = d["timestamp"].dt.tz_convert(zone).dt.date.nunique()

    rows = {}
    for machine, g in d.groupby("machine_code"):

        # drop blank readings
        c = g["cutting_min"].dropna()
        if c.empty:
            rows[machine] = {"now_h": float("nan"), "per_day_h": float("nan")}
            continue

        added_h = (c.iloc[-1] - c.iloc[0]) / 60
        rows[machine] = {
            "now_h": c.iloc[-1] / 60,
            "per_day_h": added_h / days,
        }

    return pd.DataFrame(rows).T.reindex(machines)


def hours_from_rollup(rows):
    # the same table as cutting_hours, from the daily totals
    # finished days give the rate. Today only counts while there is nothing else.

    machines = sorted(rows["machine"].unique())
    out = {}

    for machine, g in rows.groupby("machine"):
        known = g.dropna(subset=["cut_first_min", "cut_last_min"]).sort_values("work_date")
        if known.empty:
            out[machine] = {"now_h": float("nan"), "per_day_h": float("nan")}
            continue

        finished = known.iloc[:-1] if len(known) > 1 else known
        added_h = (finished["cut_last_min"].iloc[-1] - finished["cut_first_min"].iloc[0]) / 60
        out[machine] = {
            "now_h": known["cut_last_min"].iloc[-1] / 60,
            "per_day_h": added_h / len(finished),
        }

    return pd.DataFrame(out).T.reindex(machines)


def maintenance_due(hours, jobs, last_done):

    rows = []

    for machine, h in hours.iterrows():
        for j in jobs:

            row = {"machine": machine, "job": j["job"], "every_h": j["every_h"]}

            # machine does not report cutting hours
            if pd.isna(h["now_h"]):
                row.update(used_h=None, left_h=None, days_left=None, status="NO CUTTING HOURS")
                rows.append(row)
                continue

            done = last_done.get(machine, {}).get(j["job"])
            if done is None:
                row.update(used_h=None, left_h=None, days_left=None, status="NO RECORD")
                rows.append(row)
                continue

            used = h["now_h"] - done
            left = j["every_h"] - used

            if left <= 0:
                status = "OVERDUE"
            elif left <= j["every_h"] * SOON_SHARE:
                status = "DUE SOON"
            else:
                status = "OK"

            # days left at the recent cutting rate
            rate = h["per_day_h"]
            days_left = round(left / rate, 1) if left > 0 and rate > 0 else None

            row.update(
                used_h=round(used, 1),
                left_h=round(left, 1),
                days_left=days_left,
                status=status,
            )
            rows.append(row)

    # always the same columns, even for a client with no maintenance jobs
    return pd.DataFrame(rows, columns=["machine", "job", "every_h", "used_h", "left_h", "days_left", "status"])


if __name__ == "__main__":

    s = load_settings("settings/example_client.json")

    df = pd.read_csv("data/cnc_sim_data.csv", low_memory=False)

    hours = cutting_hours(df, s["timezone"])

    print("cutting hours:")
    print(hours.round(1).to_string())
    print()

    print("service status:")
    print(maintenance_due(hours, s["maintenance_jobs"], s["last_done"]).to_string(index=False))