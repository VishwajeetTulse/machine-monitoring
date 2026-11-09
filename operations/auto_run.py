# runs the whole system again and again, so nobody has to type commands

import argparse
import time
from datetime import datetime

import alerts
from run_all import run
from settings import load_settings


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
    p.add_argument("--fresh", action="store_true", help="delete old alerts before starting")
    a = p.parse_args()

    settings = load_settings(a.settings)

    if a.fresh:
        alerts.reset()

    print(f"running every {a.every:g} seconds from the {a.source} source. Ctrl+C to stop.")

    count = 0
    try:
        while True:
            now = datetime.now().strftime("%H:%M:%S")
            try:
                tables = run(a.settings, a.data, a.rejects, a.source)
                print(f"{now}  {one_line(tables)}")

                # alerts only make sense on live readings
                if a.source == "live":
                    opened, cleared = alerts.check(settings)
                    for c in opened:
                        print(f"          ALERT    {c['rule']:<12}{c['message']}")
                    for c in cleared:
                        print(f"          cleared  {c['rule']:<12}{c['message']}")
            except Exception as e:
                # keep going. A bad moment must not stop the loop.
                print(f"{now}  not saved: {type(e).__name__}: {e}")

            count += 1
            if a.times and count >= a.times:
                break
            time.sleep(a.every)
    except KeyboardInterrupt:
        print("stopped")