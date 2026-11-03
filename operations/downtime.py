# groups the machine stops by their reason
# calculate and add the time of their stops
# aggregate those and tell the reason of downtime (time wastage) 

import pandas as pd

from test_classification import classify_frame, IDLE, SETUP
from state_events import to_events, mark_setup

def downtime_by_reason(events):

    stops = events[events["state"].isin([IDLE, SETUP])].copy()
    stops["reason"] = stops["reason"].fillna("UNKNOWN")

    table = stops.groupby("reason").agg(
        stops=("duration_s", "size"),      # how many stops
        seconds=("duration_s", "sum"),     # total time
    )

    table["minutes"] = (table["seconds"] / 60).round(1)

    table["share_pct"] = (table["seconds"] / table["seconds"].sum() * 100).round(1)

    # average length of one stop
    table["avg_stop_s"] = (table["seconds"] / table["stops"]).round(0)

    # biggest loss
    table = table.sort_values("seconds", ascending=False)
 
    return table[["stops", "minutes", "share_pct", "avg_stop_s"]]


if __name__ == "__main__":
 
    df = pd.read_csv("data/cnc_sim_data.csv", low_memory=False)
 
    # states from operation 1
    df["state"] = classify_frame(df)
 
    events = mark_setup(to_events(df))
 
    print("all machines:")
    print(downtime_by_reason(events).to_string())
    print()
 
    print("V60 only:")
    print(downtime_by_reason(events[events["machine"] == "V60"]).to_string())