import pandas as pd

df = pd.read_csv("data/cnc_sim_data.csv", low_memory=False)

print("rows:", len(df))
print("machines:", df.machine_code.unique())
print()
print("columns:")
print(df.columns.tolist())
print()
print("states:")
print(df.state.value_counts())
print()
print("downtime reasons:")
print(df.downtime_reason.value_counts())
print()
print("one minute of V60:")
print(df[df.machine_code == "V60"][
    ["timestamp","state","execution","spindle_speed","spindle_load","parts_today"]
].head(60).to_string())