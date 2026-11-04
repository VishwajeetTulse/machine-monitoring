# finds machines that are getting slower part after part

import pandas as pd

from part_counting import to_parts

# average of this many latest parts
WINDOW = 10

# slower than ideal by this much (%)
WATCH_PCT = 5
ACT_PCT = 10


def drift(parts):

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
        if n < WINDOW:
            row.update(start_pct=None, now_pct=None, crossed_watch_at=None,
                       crossed_act_at=None, status="TOO FEW PARTS")
            rows.append(row)
            continue

        # average of the last WINDOW parts, at every part
        avg = g["slower_pct"].rolling(WINDOW).mean().reset_index(drop=True)

        start = avg.iloc[WINDOW - 1]
        now = avg.iloc[-1]

        # first part number where the average passed each limit
        watch_at = avg[avg >= WATCH_PCT].index.min()
        act_at = avg[avg >= ACT_PCT].index.min()

        if now >= ACT_PCT:
            status = "ACT"
        elif now >= WATCH_PCT:
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

    return pd.DataFrame(rows)


if __name__ == "__main__":

    df = pd.read_csv("data/cnc_sim_data.csv", low_memory=False)

    parts = to_parts(df)

    table = drift(parts)

    print(f"average of last {WINDOW} parts. watch at {WATCH_PCT}%, act at {ACT_PCT}%")
    print()
    print(table.drop(columns="started_at").to_string(index=False))