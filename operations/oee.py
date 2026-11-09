# OEE = availability x performance x quality

import pandas as pd
 
from test_classification import classify_frame
from state_events import to_events, mark_setup
from part_counting import to_parts
from availability import availability
from performance import performance
from settings import load_settings

def oee(events, parts, setup_is_planned, rejects=None):
 
    a = availability(events, setup_is_planned)
    p = performance(events, parts)
 
    machines = [m for m in a.index if m != "ALL"]
 
    # machines that made parts can have an OEE
    # and only if the ideal time of every part is known
    counted = [m for m in machines if p.loc[m, "parts"] > 0 and pd.notna(p.loc[m, "ideal_s"])]

    if rejects is None:
        entered = pd.Series(dtype=float)
    else:
        entered = rejects.groupby("machine")["rejects"].sum()
    rej = entered.reindex(a.index)
    measured = rej.notna()
    rej = rej.fillna(0)

    t = pd.DataFrame(index=a.index)
    t["run_s"] = a["run_s"]
    t["planned_s"] = a["planned_s"]
    t["ideal_s"] = p["ideal_s"]
    t["parts"] = p["parts"].astype(float)
    t["rejects"] = rej

    t.loc["ALL"] = [
        t.loc[counted, "run_s"].sum(),
        t.loc[counted, "planned_s"].sum(),
        t.loc[counted, "ideal_s"].sum(),
        t.loc[counted, "parts"].sum(),
        t.loc[counted, "rejects"].sum(),
    ]
    measured["ALL"] = bool(counted) and measured[counted].all()
 
    has_parts = t["parts"] > 0

    avail = t["run_s"] / t["planned_s"].where(t["planned_s"] > 0)
    perf = (t["ideal_s"] / t["run_s"].where(t["run_s"] > 0)).where(has_parts)
    qual = ((t["parts"] - t["rejects"]) / t["parts"].where(has_parts)).where(has_parts)
 
    oee_f = avail * perf * qual

    assert (abs(avail[counted] * 100 - a.loc[counted, "availability_pct"]) <= 0.051).all()
    assert (abs(perf[counted] * 100 - p.loc[counted, "performance_pct"]) <= 0.051).all()
 
    out = pd.DataFrame({
        "availability_pct": (avail * 100).round(1),
        "performance_pct": (perf * 100).round(1),
        "quality_pct": (qual * 100).round(1),
        "oee_pct": (oee_f * 100).round(1),
    })

    # warnings
    notes = []
    for m in out.index:
        n = []
        if not has_parts[m]:
            n.append("no part data")
        elif m != "ALL" and m not in counted:
            n.append(p.loc[m, "note"])
        elif not measured[m]:
            n.append("quality not measured")
        if out.loc[m, "oee_pct"] > 90:
            n.append("over 90%, check inputs")
        notes.append("; ".join(n))
    out["note"] = notes
 
    return out

if __name__ == "__main__":

    s = load_settings("settings/example_client.json")
    planned = s["setup_is_planned"]

    df = pd.read_csv("data/cnc_sim_data.csv", low_memory=False)
 
    df["state"] = classify_frame(df)
 
    events = mark_setup(to_events(df), setup_min_seconds=s["setup_min_seconds"])
    parts = to_parts(df)
 
    # the value from the settings file
    print(f"no rejects entered, setup_is_planned = {planned} (settings file):")
    print(oee(events, parts, setup_is_planned=planned).to_string())
    print()
 
    # the other way, to compare
    print(f"no rejects entered, setup_is_planned = {not planned}:")
    print(oee(events, parts, setup_is_planned=not planned).to_string())
    print()
 
    example = pd.DataFrame({
        "machine": ["V60", "V61", "V62"],
        "rejects": [0, 6, 1],
    })
    print("example rejects (made up):")
    print(oee(events, parts, rejects=example, setup_is_planned=planned).to_string())