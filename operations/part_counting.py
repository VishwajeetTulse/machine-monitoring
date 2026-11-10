# counts how many parts the machine made, and how much time each work took
# measures the cycle and cycle times

import pandas as pd

def to_parts(df):

    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    # sort by machine
    df = df.sort_values(["machine_code", "timestamp"]).reset_index(drop=True)

    by_machine = df.groupby("machine_code")

    change = by_machine["parts_today"].diff()

    was_running = by_machine["execution"].shift() == "ACTIVE"

    # if the counter increased, a part is finished
    counter_up = change > 0

    # if counter went to 0
    reset_after_cycle = (change < 0) & was_running & (df["mode"] != "OFF")

    # reset row has the next program's values
    # take program and ideal from the row above
    for col in ["program_number", "ideal_cycle_ms"]:
        df[col] = df[col].where(~reset_after_cycle, by_machine[col].shift())

    finished = df[counter_up | reset_after_cycle].copy()

    # .loc keeps only finished rows
    finished["parts_added"] = change.where(counter_up, 1).loc[finished.index]

    parts = pd.DataFrame({
        "machine": finished["machine_code"],
        "program_number": finished["program_number"],
        "finished_at": finished["timestamp"],
        "parts_added": finished["parts_added"].astype(int),

        # ms to sec 
        # empty when the machine or the file does not give it
        "cycle_s": pd.to_numeric(finished["last_cycle_ms"], errors="coerce") / 1000,
        "ideal_s": pd.to_numeric(finished["ideal_cycle_ms"], errors="coerce") / 1000,
    }).reset_index(drop=True)

    parts["slower_pct"] = (
        (parts["cycle_s"] - parts["ideal_s"]) / parts["ideal_s"] * 100
    ).round(1)
 
    return parts

if __name__ == "__main__":
 
    df = pd.read_csv("data/cnc_sim_data.csv", low_memory=False)
 
    parts = to_parts(df)
 
    print(f"parts found: {parts['parts_added'].sum():,}")
    print()
 
    print("parts per machine:")
    print(parts.groupby("machine")["parts_added"].sum().to_string())
    print()
 
    print("first 8 parts for V60:")
    print(parts[parts["machine"] == "V60"].head(8).to_string(index=False))
    print()
 
    print("average % slower than ideal, per machine:")
    print(parts.groupby("machine")["slower_pct"].mean().round(1).to_string())