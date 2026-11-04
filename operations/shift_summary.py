# writes the whole shift summary report of a machine 

import json

import pandas as pd

from test_classification import classify_frame, IDLE, SETUP
from state_events import to_events, mark_setup
from part_counting import to_parts
from downtime import downtime_by_reason
from availability import availability
from oee import oee
from settings import load_settings

def clock(text):
   
    hours, minutes = text.split(":")
    return pd.Timedelta(hours=int(hours), minutes=int(minutes))

def shift_windows(events, shifts, zone):
    # first and last day in the data in IST
    first = events["started_at"].min().tz_convert(zone).normalize()
    last = events["ended_at"].max().tz_convert(zone).normalize()

    windows = []

    for day in pd.date_range(first - pd.Timedelta(days=1), last, freq="D"):
        for s in shifts:
            start = day + clock(s["start"])
            end = day + clock(s["end"])
 
            # crosses midnight
            if end <= start:
                end += pd.Timedelta(days=1)
 
            # keep times in UTC like the data
            windows.append((s["name"], start.tz_convert("UTC"), end.tz_convert("UTC")))
 
    return windows

def cut_events(events, start, end):
 
    # the last second that belongs to this shift
    last_second = end - pd.Timedelta(seconds=1)
 
    # events that touch the shift
    e = events[(events["started_at"] <= last_second) & (events["ended_at"] >= start)].copy()
 
    # cut an event that starts before the shift or ends after it
    e["started_at"] = e["started_at"].where(e["started_at"] >= start, start)
    e["ended_at"] = e["ended_at"].where(e["ended_at"] <= last_second, last_second)
 
    e["duration_s"] = (
        (e["ended_at"] - e["started_at"]).dt.total_seconds() + 1
    ).astype(int)
 
    return e

def cut_parts(parts, start, end):
    # a part belongs to the shift it finished in
    return parts[(parts["finished_at"] >= start) & (parts["finished_at"] < end)]
 
 
def number(x, digits=1):
    
    return None if pd.isna(x) else round(float(x), digits)
 
 
def summarise(events, parts, shifts, setup_is_planned, zone):
 
    blocks = []
 
    for name, start, end in shift_windows(events, shifts, zone):
 
        ev = cut_events(events, start, end)
        if ev.empty:
            continue
        pt = cut_parts(parts, start, end)
 
        # reuse operations 5 and 8 on this shift only
        a = availability(ev, setup_is_planned)
        o = oee(ev, pt, setup_is_planned=setup_is_planned)
        made = pt.groupby("machine")["parts_added"].sum()
 
        for m in sorted(ev["machine"].unique()):
 
            mine = ev[ev["machine"] == m]
            stops = mine[mine["state"].isin([IDLE, SETUP])]
            row = o.loc[m]
 
            # from operation 4
            top = downtime_by_reason(mine).head(3)
 
            # longest single stop
            longest = None
            if not stops.empty:
                big = stops.loc[stops["duration_s"].idxmax()]
                longest = {
                    "reason": big["reason"] if pd.notna(big["reason"]) else "UNKNOWN",
                    "minutes": round(big["duration_s"] / 60, 1),
                    "started_ist": big["started_at"].tz_convert(zone).strftime("%H:%M"),
                }
 
            notes = [n for n in row["note"].split("; ") if n]
 
            blocks.append({
                "machine": m,
                "shift": name,
                "date": start.tz_convert(zone).strftime("%Y-%m-%d"),
                "window": f"{start.tz_convert(zone):%H:%M}-{end.tz_convert(zone):%H:%M}",
                "parts": int(made.get(m, 0)),
                "run_min": number(a.loc[m, "run_s"] / 60),
                "planned_min": number(a.loc[m, "planned_s"] / 60),
                "availability_pct": number(row["availability_pct"]),
                "performance_pct": number(row["performance_pct"]),
                "quality_pct": number(row["quality_pct"]),
                "oee_pct": number(row["oee_pct"]),
                "stops": int(len(stops)),
                "downtime_min": round(stops["duration_s"].sum() / 60, 1),
                "top_reasons": [
                    {"reason": r, "minutes": float(top.loc[r, "minutes"])}
                    for r in top.index
                ],
                "longest_stop": longest,
                "notes": notes,
            })
 
    return blocks

if __name__ == "__main__":

    s = load_settings("settings/example_client.json")
 
    df = pd.read_csv("data/cnc_sim_data.csv", low_memory=False)
 
    df["state"] = classify_frame(df)
 
    events = mark_setup(to_events(df), setup_min_seconds=s["setup_min_seconds"])
    parts = to_parts(df)
 
    blocks = summarise(events, parts, s["shifts"], s["setup_is_planned"], s["timezone"])
 
    cols = ["machine", "shift", "parts", "availability_pct", "performance_pct",
            "oee_pct", "stops", "downtime_min"]
    print(pd.DataFrame(blocks)[cols].to_string(index=False))
    print()
 
    print("block for V61:")
    v61 = next(b for b in blocks if b["machine"] == "V61")
    print(json.dumps(v61, indent=2))
    print()