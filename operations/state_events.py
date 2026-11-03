# reduce the rows and show only the essential ones
# show how much the machine was in idle, how many time it stopped and how much time took to make a specific part

import pandas as pd
from test_classification import classify_frame, IDLE, SETUP, OFF

# must be at least 5 mins
SETUP_MIN_SECONDS = 300

def to_events(df):
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values(["machine_code", "timestamp"]).reset_index(drop=True)

    is_new_period = (
        (df["state"] != df["state"].shift())
        | (df["machine_code"] != df["machine_code"].shift())
    )

    df["period"] = is_new_period.cumsum()

    events = (
        df.groupby("period")

        .agg(
            machine=("machine_code", "first"),
            state=("state", "first"),
 
            started_at=("timestamp", "first"),
            ended_at=("timestamp", "last"),
 
            readings=("timestamp", "size"),
 
            program_number=("program_number", "first"),
            parts_at_end=("parts_today", "last"),
            reason=("downtime_reason", "first"),
        )

        .reset_index(drop=True)
    )

    events["duration_s"] = (
        (events["ended_at"] - events["started_at"]).dt.total_seconds() + 1
    ).astype(int)
 
    return events

def mark_setup(events):
 
    events = events.copy()
 
    # the event before on the same machine
    prev_state = events.groupby("machine")["state"].shift()
    prev_program = events.groupby("machine")["program_number"].shift()
 
    # & = and. all four must be true.
    is_setup = (
        (events["state"] == IDLE)                          # called IDLE now
        & (events["duration_s"] >= SETUP_MIN_SECONDS)      # long stop
        & (events["program_number"] != prev_program)       # program changed
        & (prev_state != OFF)                              # not after lunch
    )
 
    events.loc[is_setup, "state"] = SETUP
 
    return events

if __name__ == "__main__":
 
    df = pd.read_csv("data/cnc_sim_data.csv", low_memory=False)
 
    truth = df["state"].copy()
 
    # from operation 1
    df["state"] = classify_frame(df)
 
    events = mark_setup(to_events(df))
 
    print(f"readings in : {len(df):,}")
    print(f"events out  : {len(events):,}")
    print(f"reduction   : {len(df) / len(events):.0f}x")
    print()
 
    print("events per state:")
    print(events["state"].value_counts().to_string())
    print()
 
    print("first 12 events for V60:")
    cols = ["state", "started_at", "ended_at", "duration_s", "reason"]
    print(events[events["machine"] == "V60"][cols].head(12).to_string(index=False))
    print()
 
    real_seconds = (truth == "SETUP").sum()
    found_seconds = events.loc[events["state"] == SETUP, "duration_s"].sum()
    print(f"setup seconds, real : {real_seconds:,}")
    print(f"setup seconds, found: {found_seconds:,}")