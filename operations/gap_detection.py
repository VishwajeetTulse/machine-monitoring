# finds the periods where readings are missing and marks them NO_DATA.

import pandas as pd

from test_classification import classify_frame, NO_DATA
from state_events import to_events, mark_setup
from availability import availability

GAP_SECONDS = 5


def find_gaps(df):

    d = df[["machine_code", "timestamp"]].copy()
    d["timestamp"] = pd.to_datetime(d["timestamp"])

    # each machine's readings, in time order
    d = d.sort_values(["machine_code", "timestamp"])

    # seconds since the reading before, per machine
    d["since_last"] = d.groupby("machine_code")["timestamp"].diff().dt.total_seconds()

    gaps = d[d["since_last"] > GAP_SECONDS].copy()

    # the first and last missing second
    # normal spacing is 1 second, so one less than the wait is missing
    gaps["gap_start"] = gaps["timestamp"] - pd.to_timedelta(gaps["since_last"] - 1, unit="s")
    gaps["gap_end"] = gaps["timestamp"] - pd.Timedelta(seconds=1)
    gaps["missing_s"] = (gaps["since_last"] - 1).astype(int)

    return gaps[["machine_code", "gap_start", "gap_end", "missing_s"]].rename(
        columns={"machine_code": "machine"}
    ).reset_index(drop=True)


def fill_gaps(df):
    # call this AFTER classify_frame, so the new rows keep the NO_DATA state

    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    gaps = find_gaps(df)
    if gaps.empty:
        return df

    first = pd.DataFrame({
        "machine_code": gaps["machine"],
        "timestamp": gaps["gap_start"],
        "state": NO_DATA,
    })
    last = pd.DataFrame({
        "machine_code": gaps["machine"],
        "timestamp": gaps["gap_end"],
        "state": NO_DATA,
    })

    both = pd.concat([df, first, last], ignore_index=True)
    return both.sort_values(["machine_code", "timestamp"]).reset_index(drop=True)


def coverage(events):

    # seconds per machine, one column per state
    seconds = events.groupby(["machine", "state"])["duration_s"].sum().unstack(fill_value=0)

    # a machine with no gaps has no NO_DATA column. Use 0 then.
    no_data = seconds.get(NO_DATA, pd.Series(0, index=seconds.index))
    total = seconds.sum(axis=1)

    holes = events[events["state"] == NO_DATA]

    table = pd.DataFrame({
        "gaps": holes.groupby("machine").size(),
        "missing_min": (no_data / 60).round(1),
        "longest_min": (holes.groupby("machine")["duration_s"].max() / 60).round(1),
        "coverage_pct": ((1 - no_data / total) * 100).round(1),
    })

    table["gaps"] = table["gaps"].fillna(0).astype(int)
    table["longest_min"] = table["longest_min"].fillna(0)

    return table


if __name__ == "__main__":

    df = pd.read_csv("data/cnc_sim_data.csv", low_memory=False)

    df["state"] = classify_frame(df)

    print("gaps in the real file:", len(find_gaps(df)))
    print()

    # test: delete 10 minutes of V61 readings, 3 hours into the day
    t = pd.to_datetime(df["timestamp"])
    cut_from = t.min() + pd.Timedelta(hours=3)
    cut = (df["machine_code"] == "V61") & (t >= cut_from) & (t < cut_from + pd.Timedelta(minutes=10))
    broken = df[~cut]
    print(f"deleted {cut.sum()} readings of V61")
    print()

    print("gaps found:")
    print(find_gaps(broken).to_string(index=False))
    print()

    ev_whole = mark_setup(to_events(df))
    ev_plain = mark_setup(to_events(broken))
    ev_marked = mark_setup(to_events(fill_gaps(broken)))

    print("coverage:")
    print(coverage(ev_marked).to_string())
    print()

    # seconds in the events table for V61. A full day is 28,800.
    def seconds(ev):
        return int(ev[ev["machine"] == "V61"]["duration_s"].sum())

    print("V61 seconds in events:")
    print("  full data         ", seconds(ev_whole))
    print("  gap, not marked   ", seconds(ev_plain))
    print("  gap, marked       ", seconds(ev_marked))
    print()

    # availability of V61
    def avail(ev):
        return availability(ev).loc["V61", "availability_pct"]

    print("V61 availability:")
    print("  full data         ", avail(ev_whole))
    print("  gap, not marked   ", avail(ev_plain))
    print("  gap, marked       ", avail(ev_marked))