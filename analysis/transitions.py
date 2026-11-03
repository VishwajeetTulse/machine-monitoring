import pandas as pd

df = pd.read_csv("data/cnc_sim_data.csv", low_memory=False)
v60 = df[df.machine_code == "V60"].reset_index(drop=True)

changed = v60[v60.state != v60.state.shift()]

print("state changes for V60:")
print(changed[["timestamp","state","downtime_reason","execution","mode",
               "spindle_speed","spindle_load","parts_today"]].head(40).to_string())