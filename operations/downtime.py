import pandas as pd
 
from test_classification import classify_frame, IDLE, SETUP
from state_events import to_events, mark_setup
from settings import load_settings
 
 
def downtime_by_reason(events):
 
    # keep only the stops
    # OFF is not here: lunch is planned, not a loss
    stops = events[events["state"].isin([IDLE, SETUP])].copy()
 
    # a stop with no reason still counts
    stops["reason"] = stops["reason"].fillna("UNKNOWN")
 
    # one row per reason
    table = stops.groupby("reason").agg(
        stops=("duration_s", "size"),      # how many stops
        seconds=("duration_s", "sum"),     # total time
    )
 
    # seconds -> minutes
    table["minutes"] = (table["seconds"] / 60).round(1)
 
    # this reason's part of all downtime, in percent
    table["share_pct"] = (table["seconds"] / table["seconds"].sum() * 100).round(1)
 
    # average length of one stop
    table["avg_stop_s"] = (table["seconds"] / table["stops"]).round(0)
 
    # biggest loss on top
    table = table.sort_values("seconds", ascending=False)
 
    return table[["stops", "minutes", "share_pct", "avg_stop_s"]]
 
 
if __name__ == "__main__":
 
    s = load_settings("settings/example_client.json")
 
    df = pd.read_csv("data/cnc_sim_data.csv", low_memory=False)
 
    # our own states from operation 1
    df["state"] = classify_frame(df)
 
    events = mark_setup(to_events(df), setup_min_seconds=s["setup_min_seconds"])
 
    print("all machines:")
    print(downtime_by_reason(events).to_string())
    print()
 
    # one machine only
    print("V60 only:")
    print(downtime_by_reason(events[events["machine"] == "V60"]).to_string())