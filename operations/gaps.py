# finds the periods where readings are missing and marks them NO_DATA.

import pandas as pd

from test_classification import classify_frame, NO_DATA, NOT_RESPONDING, PLANNED_STOP
from state_events import to_events, mark_setup
from availability import availability
from settings import load_settings


def find_gaps(df, gap_seconds):

    d = df[["machine_code", "timestamp"]].copy()
    d["timestamp"] = pd.to_datetime(d["timestamp"])

    # each machine's readings, in time order
    d = d.sort_values(["machine_code", "timestamp"])

    # seconds since the reading before, per machine
    d["since_last"] = d.groupby("machine_code")["timestamp"].diff().dt.total_seconds()

    gaps = d[d["since_last"] > gap_seconds].copy()

    # the first and last missing second
    # normal spacing is 1 second, so one less than the wait is missing
    gaps["gap_start"] = gaps["timestamp"] - pd.to_timedelta(gaps["since_last"] - 1, unit="s")
    gaps["gap_end"] = gaps["timestamp"] - pd.Timedelta(seconds=1)
    gaps["missing_s"] = (gaps["since_last"] - 1).astype(int)

    return gaps[["machine_code", "gap_start", "gap_end", "missing_s"]].rename(
        columns={"machine_code": "machine"}
    ).reset_index(drop=True)


def healthy_windows(heartbeat, every=5, max_spacing=15):
    # when the box was running and could reach the network: [(from, to), ...]
    # no heartbeats (a replay or a CSV file) means nothing is known, so every gap is NO_DATA

    if heartbeat is None or heartbeat.empty:
        return []

    beats = pd.to_datetime(heartbeat[heartbeat["network_ok"] == 1]["timestamp"], utc=True).sort_values()
    windows = []
    for t in beats:
        if windows and (t - windows[-1][1]).total_seconds() <= max_spacing:
            windows[-1][1] = t
        else:
            windows.append([t, t])

    # a beat speaks for the seconds up to the next one
    return [(a, b + pd.Timedelta(seconds=every - 1)) for a, b in windows]


def split_by_health(start, end, healthy):
    # cuts a silent period into the part where the box was fine and the part where it was not
    out = []
    cursor = start
    one = pd.Timedelta(seconds=1)

    for good_from, good_to in healthy or []:
        if good_to < cursor:
            continue
        if good_from > end:
            break
        if good_from > cursor:
            out.append((cursor, good_from - one, NO_DATA))
            cursor = good_from
        stop = min(good_to, end)
        out.append((cursor, stop, NOT_RESPONDING))
        cursor = stop + one
        if cursor > end:
            return out

    if cursor <= end:
        out.append((cursor, end, NO_DATA))
    return out


def fill_gaps(df, gap_seconds, healthy=None, start=None, end=None, machines=None):
    # call this AFTER classify_frame, so the new rows keep their state
    # with start and end, silence before the first and after the last reading counts too,
    # and so does a machine in `machines` that has no readings at all

    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

    gaps = find_gaps(df, gap_seconds)
    spans = list(zip(gaps["machine"], gaps["gap_start"], gaps["gap_end"]))

    if end is not None:
        one = pd.Timedelta(seconds=1)
        for machine in sorted(set(df["machine_code"]) | set(machines or [])):
            times = df.loc[df["machine_code"] == machine, "timestamp"]
            if times.empty:
                spans.append((machine, start, end))
                continue
            if (times.min() - start).total_seconds() > gap_seconds:
                spans.append((machine, start, times.min() - one))
            if (end - times.max()).total_seconds() > gap_seconds:
                spans.append((machine, times.max() + one, end))

    rows = []
    for machine, gap_start, gap_end in spans:
        for a, b, state in split_by_health(gap_start, gap_end, healthy):
            rows.append({"machine_code": machine, "timestamp": a, "state": state})
            rows.append({"machine_code": machine, "timestamp": b, "state": state})

    if not rows:
        return df

    added = pd.DataFrame(rows)
    added["timestamp"] = pd.to_datetime(added["timestamp"], utc=True)

    both = pd.concat([df, added], ignore_index=True)
    return both.sort_values(["machine_code", "timestamp"]).reset_index(drop=True)


def coverage(events):

    # seconds per machine, one column per state
    seconds = events.groupby(["machine", "state"])["duration_s"].sum().unstack(fill_value=0)

    # a machine with no gaps has no NO_DATA column. Use 0 then.
    no_data = seconds.get(NO_DATA, pd.Series(0, index=seconds.index))
    # share of the working time. Breaks do not count either way.
    total = seconds.sum(axis=1) - seconds.get(PLANNED_STOP, pd.Series(0, index=seconds.index))

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

    s = load_settings("settings/example_client.json")
    gap = s["gap_seconds"]

    df = pd.read_csv("data/cnc_sim_data.csv", low_memory=False)

    df["state"] = classify_frame(df)

    print("gaps in the real file:", len(find_gaps(df, gap)))
    print()

    # test: delete 10 minutes of V61 readings, 3 hours into the day
    t = pd.to_datetime(df["timestamp"])
    cut_from = t.min() + pd.Timedelta(hours=3)
    cut = (df["machine_code"] == "V61") & (t >= cut_from) & (t < cut_from + pd.Timedelta(minutes=10))
    broken = df[~cut]
    print(f"deleted {cut.sum()} readings of V61")
    print()

    print("gaps found:")
    print(find_gaps(broken, gap).to_string(index=False))
    print()

    ev_whole = mark_setup(to_events(df), setup_min_seconds=s["setup_min_seconds"])
    ev_plain = mark_setup(to_events(broken), setup_min_seconds=s["setup_min_seconds"])
    ev_marked = mark_setup(to_events(fill_gaps(broken, gap)), setup_min_seconds=s["setup_min_seconds"])

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
        return availability(ev, s["setup_is_planned"]).loc["V61", "availability_pct"]

    print("V61 availability:")
    print("  full data         ", avail(ev_whole))
    print("  gap, not marked   ", avail(ev_plain))
    print("  gap, marked       ", avail(ev_marked))