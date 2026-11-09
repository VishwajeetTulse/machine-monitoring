# determine how much time the machine was actually running

import pandas as pd

from test_classification import (
    classify_frame, RUNNING, IDLE, SETUP, ALARM, OFF, NO_DATA, NOT_RESPONDING, PLANNED_STOP,
)
from state_events import to_events, mark_setup
from settings import load_settings

def availability(events, setup_is_planned):
 
    # total seconds per machine
    seconds = (
        events.groupby(["machine", "state"])["duration_s"]
        .sum()
        .unstack(fill_value=0)
    )
 
    # all states exist
    seconds = seconds.reindex(
        columns=[RUNNING, IDLE, SETUP, ALARM, OFF, NO_DATA, NOT_RESPONDING, PLANNED_STOP],
        fill_value=0,
    )

    table = pd.DataFrame(index=seconds.index)
    table["run_s"] = seconds[RUNNING]

    # breaks and missing data are not planned time
    # a machine that did not answer during working time is: that time was lost
    planned = seconds.sum(axis=1) - seconds[OFF] - seconds[NO_DATA] - seconds[PLANNED_STOP]

    if setup_is_planned:
        planned = planned - seconds[SETUP]
 
    table["planned_s"] = planned

    table.loc["ALL"] = [table["run_s"].sum(), table["planned_s"].sum()]

    table["availability_pct"] = (
        table["run_s"] / table["planned_s"].where(table["planned_s"] > 0) * 100
    ).round(1)
 
    return table

if __name__ == "__main__":

    s = load_settings("settings/example_client.json")

    df = pd.read_csv("data/cnc_sim_data.csv", low_memory=False)

    df["state"] = classify_frame(df)

    events = mark_setup(to_events(df), setup_min_seconds=s["setup_min_seconds"])

    print("from settings file, setup_is_planned =", s["setup_is_planned"])
    print(availability(events, setup_is_planned=s["setup_is_planned"]).to_string())