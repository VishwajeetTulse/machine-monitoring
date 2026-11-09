# the readings table: one row = one second of one machine
# same table the replay fills, so everything else keeps working

import sqlite3
from pathlib import Path

# column name -> type. Fixed, so an empty first batch cannot confuse the table.
COLUMNS = {
    "timestamp": "TEXT", "machine_code": "TEXT", "machine_id": "TEXT",
    "downtime_reason": "TEXT", "execution": "TEXT", "mode": "TEXT",
    "program_name": "TEXT", "program_number": "INTEGER", "part_name": "TEXT",
    "spindle_speed": "INTEGER", "spindle_load": "REAL", "spindle_power": "INTEGER",
    "spindle_temp": "REAL", "max_rpm": "INTEGER", "loop_deviation": "INTEGER",
    "parts_today": "INTEGER", "parts_life": "INTEGER",
    "last_cycle_ms": "INTEGER", "ideal_cycle_ms": "INTEGER",
    "cutting_min": "INTEGER", "operating_min": "INTEGER", "poweron_min": "INTEGER",
    "override_feed": "INTEGER", "alarm_code": "INTEGER", "alarm_text": "REAL",
}

# only the simulator knows these. A real machine cannot say why it stopped.
SIMULATOR_ANSWERS = ["downtime_reason"]

# what a machine may be unable to report, and the columns that depend on it
CAPABILITY_COLUMNS = {
    "part_count": ["parts_today", "parts_life"],
    "cycle_time": ["last_cycle_ms"],
    "cutting_hours": ["cutting_min"],
}


def check_capabilities(code, capabilities):
    # a misspelt name would be ignored silently, so stop on it
    unknown = set(capabilities) - set(CAPABILITY_COLUMNS)
    if unknown:
        raise ValueError(f"machine {code}: unknown capability: {sorted(unknown)}")
    for name, value in capabilities.items():
        if not isinstance(value, bool):
            raise ValueError(f"machine {code}: capability {name} must be true or false")


def unsupported_columns(capabilities):
    # a capability that is not mentioned counts as available
    columns = []
    for name, depends in CAPABILITY_COLUMNS.items():
        if not capabilities.get(name, True):
            columns += depends
    return columns


def blank_unsupported(row, columns):
    # empty, never zero, so the calculations can say "no data"
    for c in columns:
        row[c] = None
    return row


def open_db(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)

    # one writer and many readers can work together
    con.execute("PRAGMA journal_mode=WAL")

    cols = ", ".join(f'"{c}" {t}' for c, t in COLUMNS.items())
    con.execute(f"CREATE TABLE IF NOT EXISTS readings ({cols})")
    con.execute("CREATE INDEX IF NOT EXISTS idx_readings ON readings (machine_code, timestamp)")
    con.commit()
    return con


def write(con, rows):
    # rows = list of dicts. A missing value is saved as empty (NULL).
    if not rows:
        return
    names = list(COLUMNS)
    marks = ", ".join("?" for _ in names)
    con.executemany(
        f"INSERT INTO readings ({', '.join(chr(34) + n + chr(34) for n in names)}) VALUES ({marks})",
        [[row.get(n) for n in names] for row in rows],
    )
    con.commit()