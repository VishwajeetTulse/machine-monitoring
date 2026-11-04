# determine how much time the machine was actually running

import pandas as pd 

from test_classification import classify_frame, RUNNING, IDLE, SETUP, ALARM, OFF
from state_events import to_events, mark_setup

SETUP_IS_PLANNED = False

def availability(events, setup_is_planned=SETUP_IS_PLANNED):
 
    # total seconds per machine
    seconds = (
        events.groupby(["machine", "state"])["duration_s"]
        .sum()
        .unstack(fill_value=0)
    )
 
    # all five states exist
    seconds = seconds.reindex(columns=[RUNNING, IDLE, SETUP, ALARM, OFF], fill_value=0)

    table = pd.DataFrame(index=seconds.index)
    table["run_s"] = seconds[RUNNING]

    planned = seconds.sum(axis=1) - seconds[OFF]

    if setup_is_planned:
        planned = planned - seconds[SETUP]
 
    table["planned_s"] = planned

    table.loc["ALL"] = [table["run_s"].sum(), table["planned_s"].sum()]

    table["availability_pct"] = (
        table["run_s"] / table["planned_s"].where(table["planned_s"] > 0) * 100
    ).round(1)
 
    return table

if __name__ == "__main__":
 
    df = pd.read_csv("data/cnc_sim_data.csv", low_memory=False)
 
    df["state"] = classify_frame(df)
 
    events = mark_setup(to_events(df))
 
    print("setup counts as a loss:")
    print(availability(events, setup_is_planned=False).to_string())
    print()
 
    print("setup is planned:")
    print(availability(events, setup_is_planned=True).to_string())