import pandas as pd
 
from test_classification import classify_frame, RUNNING
from state_events import to_events, mark_setup
from settings import load_settings
from part_counting import to_parts
 
 
def performance(events, parts):
 
    # seconds spent RUNNING, per machine
    run = events[events["state"] == RUNNING].groupby("machine")["duration_s"].sum()
 
    # parts made, and the ideal time for all of them, per machine
    made = parts.groupby("machine").agg(
        parts=("parts_added", "sum"),
        ideal_s=("ideal_s", "sum"),
    )
 
    # one table. A machine with no parts gets 0 here.
    table = pd.DataFrame({
        "parts": made["parts"],
        "ideal_s": made["ideal_s"],
        "run_s": run,
    }).fillna(0)
    table["parts"] = table["parts"].astype(int)
 
    # one row for all machines together
    # only machines that made parts, or the run time would be too big
    counted = table[table["parts"] > 0]
    table.loc["ALL"] = [counted["parts"].sum(), counted["ideal_s"].sum(), counted["run_s"].sum()]
    table["parts"] = table["parts"].astype(int)
 
    # percent. where(...) leaves it empty when there are no parts.
    table["performance_pct"] = (
        table["ideal_s"] / table["run_s"].where(table["run_s"] > 0) * 100
    ).where(table["parts"] > 0).round(1)
 
    # warnings
    table["note"] = ""
    table.loc[table["parts"] == 0, "note"] = "no part data"
    table.loc[table["performance_pct"] > 100, "note"] = "check ideal time"
 
    return table
 
 
if __name__ == "__main__":
 
    s = load_settings("settings/example_client.json")
 
    df = pd.read_csv("data/cnc_sim_data.csv", low_memory=False)
 
    df["state"] = classify_frame(df)
 
    events = mark_setup(to_events(df), setup_min_seconds=s["setup_min_seconds"])
    parts = to_parts(df)
 
    print(performance(events, parts).to_string())