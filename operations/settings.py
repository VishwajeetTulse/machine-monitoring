# reads one client's settings file

import json
from datetime import datetime
from zoneinfo import ZoneInfo

# values used when the file does not mention a setting
DEFAULTS = {
    "timezone": "Asia/Kolkata",
    "setup_min_seconds": 300,
    "gap_seconds": 5,
    "drift": {"window": 10, "watch_pct": 5, "act_pct": 10},
    "failure_alarm_codes": [],   # empty = every alarm counts
    "maintenance_jobs": [],
    "last_done": {},
    "stop_reasons": ["LOADING", "NO_MATERIAL", "TOOL_CHANGE", "NO_OPERATOR",
                     "QUALITY_CHECK", "SETUP", "MAINTENANCE", "OTHER"],
    "alerts": {
        "alarm_minutes": 5,
        "idle_minutes": 15,
        "silent_minutes": 2,
        "drift_levels": ["ACT"],
        "maintenance_levels": ["OVERDUE", "DUE SOON"],
    },
}

# no default. It changes the OEE number, so the file must say it
REQUIRED = ["client", "shifts", "setup_is_planned"]


def load_settings(path):

    with open(path, encoding="utf-8") as f:
        given = json.load(f)

    # a misspelt name would be ignored silently, so stop on it
    known = set(DEFAULTS) | set(REQUIRED)
    unknown = set(given) - known
    if unknown:
        raise ValueError(f"unknown setting(s): {sorted(unknown)}")

    for name in REQUIRED:
        if name not in given:
            raise ValueError(f"settings file must have: {name}")

    # file values on top of the defaults
    s = {**DEFAULTS, **given}
    s["drift"] = {**DEFAULTS["drift"], **given.get("drift", {})}
    s["alerts"] = {**DEFAULTS["alerts"], **given.get("alerts", {})}

    check(s)
    return s


def check(s):

    ZoneInfo(s["timezone"])

    if not isinstance(s["setup_is_planned"], bool):
        raise ValueError("setup_is_planned must be true or false")

    if not s["shifts"]:
        raise ValueError("shifts cannot be empty")
    for sh in s["shifts"]:
        for key in ("name", "start", "end"):
            if key not in sh:
                raise ValueError(f"every shift needs: {key}")
        for key in ("start", "end"):
            datetime.strptime(sh[key], "%H:%M")

    if s["setup_min_seconds"] <= 0 or s["gap_seconds"] <= 0:
        raise ValueError("setup_min_seconds and gap_seconds must be above 0")

    reasons = s["stop_reasons"]
    if not reasons or not all(isinstance(r, str) and r.strip() for r in reasons):
        raise ValueError("stop_reasons must be a list of names")
    if len(set(reasons)) != len(reasons):
        raise ValueError("stop_reasons has a name twice")

    d = s["drift"]
    if d["window"] < 2 or not (0 < d["watch_pct"] < d["act_pct"]):
        raise ValueError("drift: window 2 or more, and 0 < watch_pct < act_pct")

    al = s["alerts"]
    unknown = set(al) - set(DEFAULTS["alerts"])
    if unknown:
        raise ValueError(f"unknown alert setting(s): {sorted(unknown)}")
    for key in ("alarm_minutes", "idle_minutes", "silent_minutes"):
        if al[key] <= 0:
            raise ValueError(f"alerts: {key} must be above 0")
    if not set(al["drift_levels"]) <= {"WATCH", "ACT"}:
        raise ValueError("alerts: drift_levels can only hold WATCH and ACT")
    if not set(al["maintenance_levels"]) <= {"OVERDUE", "DUE SOON", "NO RECORD"}:
        raise ValueError("alerts: maintenance_levels can only hold OVERDUE, DUE SOON, NO RECORD")

    for j in s["maintenance_jobs"]:
        if "job" not in j or j.get("every_h", 0) <= 0:
            raise ValueError("every maintenance job needs a name and every_h above 0")


if __name__ == "__main__":

    s = load_settings("settings/example_client.json")

    print("client:           ", s["client"])
    print("time zone:        ", s["timezone"])
    print("shifts:           ", [f"{x['name']} {x['start']}-{x['end']}" for x in s["shifts"]])
    print("setup is planned: ", s["setup_is_planned"])
    print("setup min seconds:", s["setup_min_seconds"])
    print("gap seconds:      ", s["gap_seconds"])
    print("drift:            ", s["drift"])
    print("failure alarms:   ", s["failure_alarm_codes"] or "all alarms count")
    print("maintenance jobs: ", [j["job"] for j in s["maintenance_jobs"]])