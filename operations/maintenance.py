# tells which machine needs servicing based on cutting hours

import pandas as pd

ZONE = "Asia/Kolkata"

JOBS = [
    {"job": "Way lube check", "every_h": 300},
    {"job": "Spindle grease", "every_h": 500},
    {"job": "Coolant change", "every_h": 800},
]

LAST_DONE = {
    "V60": {"Way lube check": 400, "Spindle grease": 100, "Coolant change": 0},
    "V61": {"Way lube check": 500, "Spindle grease": 100, "Coolant change": 300},
    "V62": {"Way lube check": 200, "Spindle grease": 0},
}

# due soon when this share of the interval is left
SOON_SHARE = 0.10


def cutting_hours(df):

    d = df.copy()
    d["timestamp"] = pd.to_datetime(d["timestamp"])
    d = d.sort_values(["machine_code", "timestamp"])

    machines = sorted(d["machine_code"].unique())

    if "cutting_min" not in d.columns:
        d["cutting_min"] = float("nan")

    # working days on file in IST
    days = d["timestamp"].dt.tz_convert(ZONE).dt.date.nunique()

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


def maintenance_due(hours, jobs=JOBS, last_done=LAST_DONE):

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

    return pd.DataFrame(rows)


if __name__ == "__main__":

    df = pd.read_csv("data/cnc_sim_data.csv", low_memory=False)

    hours = cutting_hours(df)

    print("cutting hours:")
    print(hours.round(1).to_string())
    print()

    print("service status:")
    print(maintenance_due(hours).to_string(index=False))