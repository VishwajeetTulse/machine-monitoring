import numpy as np
import pandas as pd

# constants
RUNNING = "RUNNING"
IDLE = "IDLE"
SETUP = "SETUP"
ALARM = "ALARM"
OFF = "OFF"

def classify(row):
    if row["alarm_code"] != 0:
        return ALARM

    if row["mode"] == "OFF":
        return OFF

    if row["execution"] == "ACTIVE":
        return RUNNING

    return IDLE

def classify_frame(df):
    conditions = [
        df["alarm_code"] != 0,
        df["mode"] == "OFF",
        df["execution"] == "ACTIVE",
    ]

    choices = [ALARM, OFF, RUNNING]
    return pd.Series(np.select(conditions, choices, default=IDLE), index=df.index)


if __name__ == "__main__":
    df = pd.read_csv("data/cnc_sim_data.csv", low_memory=False)

    df["predicted"] = classify_frame(df)

    sample = df.sample(2000, random_state=0)
    assert (sample.apply(classify, axis=1) == sample["predicted"]).all()
 
    accuracy = (df["predicted"] == df["state"]).mean()
    print(f"accuracy: {accuracy:.1%}")
    print()
    print(pd.crosstab(df["state"], df["predicted"]))
 
    missed = df[df["predicted"] != df["state"]]
    print()
    print("missed states:")
    print(missed["state"].value_counts().to_string())