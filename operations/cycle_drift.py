# finds machines that are getting slower part after part

import pandas as pd

from part_counting import to_parts
from settings import load_settings

# always the same columns, even with no runs yet
COLUMNS = ["machine", "program", "parts", "started_at", "start_pct", "now_pct",
           "crossed_watch_at", "crossed_act_at", "status"]


def drift(parts, window, watch_pct, act_pct):

    parts = parts.sort_values(["machine", "finished_at"]).reset_index(drop=True)

    # new run when the program changes
    new_run = parts["program_number"] != parts.groupby("machine")["program_number"].shift()
    parts["run"] = new_run.groupby(parts["machine"]).cumsum()

    rows = []

    for (machine, run), g in parts.groupby(["machine", "run"]):

        n = len(g)
        row = {
            "machine": machine,
            "program": int(g["program_number"].iloc[0]),
            "parts": n,
            "started_at": g["finished_at"].iloc[0],
        }

        # not enough parts to judge
        if n < window:
            row.update(start_pct=None, now_pct=None, crossed_watch_at=None,
                       crossed_act_at=None, status="TOO FEW PARTS")
            rows.append(row)
            continue

        # average of the last window parts, at every part
        avg = g["slower_pct"].rolling(window).mean().reset_index(drop=True)

        start = avg.iloc[window - 1]
        now = avg.iloc[-1]

        # first part number where the average passed each limit
        watch_at = avg[avg >= watch_pct].index.min()
        act_at = avg[avg >= act_pct].index.min()

        if now >= act_pct:
            status = "ACT"
        elif now >= watch_pct:
            status = "WATCH"
        else:
            status = "OK"

        row.update(
            start_pct=round(start, 1),
            now_pct=round(now, 1),
            # +1 because the index starts at 0
            crossed_watch_at=None if pd.isna(watch_at) else int(watch_at) + 1,
            crossed_act_at=None if pd.isna(act_at) else int(act_at) + 1,
            status=status,
        )
        rows.append(row)

    return pd.DataFrame(rows, columns=COLUMNS)


if __name__ == "__main__":

    s = load_settings("settings/example_client.json")
    d = s["drift"]

    df = pd.read_csv("data/cnc_sim_data.csv", low_memory=False)

    parts = to_parts(df)

    table = drift(parts, d["window"], d["watch_pct"], d["act_pct"])

    print(f"average of last {d['window']} parts. watch at {d['watch_pct']}%, act at {d['act_pct']}%")
    print()
    print(table.drop(columns="started_at").to_string(index=False))