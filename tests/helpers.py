# small made-up machine days, so every expected answer can be worked out by hand

import pandas as pd

# 07:00 IST on 2 Nov 2026
START = pd.Timestamp("2026-11-02T01:30:00Z")


def stamp(t):
    return t.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def run(seconds, finished=0, ideal_s=60, cycle_s=60, program=100):
    # the machine cuts. The part counter goes up by `finished` on the LAST second.
    return {"seconds": seconds, "execution": "ACTIVE", "mode": "AUTOMATIC", "finished": finished,
            "ideal_s": ideal_s, "cycle_s": cycle_s, "program": program}


def idle(seconds, reason=None, program=100):
    return {"seconds": seconds, "execution": "STOPPED", "mode": "AUTOMATIC", "reason": reason, "program": program}


def off(seconds):
    return {"seconds": seconds, "execution": "STOPPED", "mode": "OFF", "program": 100}


def alarm(seconds, code=1234, program=100):
    return {"seconds": seconds, "execution": "STOPPED", "mode": "AUTOMATIC", "alarm": code, "program": program}


def silent(seconds):
    # no readings at all for this long
    return {"seconds": seconds, "silent": True}


def readings(machine, blocks, start=START):
    rows = []
    t = start
    parts = 0
    cycle_ms = 0

    for b in blocks:
        for i in range(b["seconds"]):
            if b.get("silent"):
                t += pd.Timedelta(seconds=1)
                continue

            last_second = i == b["seconds"] - 1
            if last_second and b.get("finished"):
                parts += b["finished"]
                cycle_ms = int(b["cycle_s"] * 1000)

            ideal = b.get("ideal_s")
            rows.append({
                "timestamp": stamp(t),
                "machine_code": machine,
                "machine_id": machine,
                "downtime_reason": b.get("reason"),
                "execution": b["execution"],
                "mode": b["mode"],
                "program_name": f"O{b['program']}",
                "program_number": b["program"],
                "parts_today": parts,
                "parts_life": parts,
                "last_cycle_ms": cycle_ms,
                "ideal_cycle_ms": None if ideal is None else int(ideal * 1000),
                "alarm_code": b.get("alarm", 0),
                "cutting_min": None,
            })
            t += pd.Timedelta(seconds=1)

    return pd.DataFrame(rows)


def together(*frames):
    return pd.concat(frames, ignore_index=True)
