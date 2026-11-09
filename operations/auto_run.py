# runs the whole system again and again

import argparse
import time
from datetime import datetime

from run_all import run, DB
from store import query


def one_line(tables):
    parts = int(tables["parts"]["parts_added"].sum())
    all_row = tables["oee"][tables["oee"]["machine"] == "ALL"].iloc[0]
    oee = all_row["oee_pct"]
    return f"parts {parts:>4}   availability {all_row['availability_pct']:>5}%   OEE {oee:>5}%"


if __name__ == "__main__":

    p = argparse.ArgumentParser()
    p.add_argument("--settings", default="settings/example_client.json")
    p.add_argument("--data", default="data/cnc_sim_data.csv")
    p.add_argument("--rejects", default=None)
    p.add_argument("--source", choices=["csv", "live"], default="live")
    p.add_argument("--every", type=float, default=10, help="seconds between runs")
    p.add_argument("--times", type=int, default=0, help="stop after this many runs (0 = never)")
    a = p.parse_args()

    print(f"running every {a.every:g} seconds from the {a.source} source. Ctrl+C to stop.")

    count = 0
    try:
        while True:
            now = datetime.now().strftime("%H:%M:%S")
            try:
                tables = run(a.settings, a.data, a.rejects, a.source)
                print(f"{now}  {one_line(tables)}")
            except Exception as e:
                print(f"{now}  not saved: {type(e).__name__}: {e}")

            count += 1
            if a.times and count >= a.times:
                break
            time.sleep(a.every)
    except KeyboardInterrupt:
        print("stopped")