import pandas as pd

df = pd.read_csv("data/cnc_sim_data.csv", low_memory=False)

print(df.groupby("state").agg(
    rows=("state","size"),
    speed_min=("spindle_speed","min"),
    speed_max=("spindle_speed","max"),
    load_min=("spindle_load","min"),
    load_max=("spindle_load","max"),
).to_string())

print()
print(pd.crosstab(df.state, df.execution))
print()
print(pd.crosstab(df.state, df["mode"]))