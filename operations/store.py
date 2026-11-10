# saves result tables into one SQLite file

import sqlite3
from pathlib import Path

import pandas as pd


def clean(df):
    df = df.copy()

    for col in df.columns:
        # times go in as text, in UTC
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.tz_convert("UTC").dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    # blank numbers become NULL
    return df.astype(object).where(df.notna(), None)


def make_table(con, name, df):
    # fixed column types. An empty column must not leave the type blank.
    if len(df.columns) == 0:
        raise ValueError(f"table {name} has no columns")
    kinds = {"i": "INTEGER", "u": "INTEGER", "f": "REAL", "b": "INTEGER"}
    cols = ", ".join(f'"{c}" {kinds.get(df[c].dtype.kind, "TEXT")}' for c in df.columns)
    con.execute(f'DROP TABLE IF EXISTS "{name}"')
    con.execute(f'CREATE TABLE "{name}" ({cols})')


def save_tables(path, tables):
    # tables = {"name": DataFrame}
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(path)
    try:
        for name, df in tables.items():
            # a new table every run, so a run never adds on top of an old one
            make_table(con, name, df)
            clean(df).to_sql(name, con, if_exists="append", index=False)
        con.commit()
    finally:
        con.close()


def add_log(path, row):
    # run_log keeps every run
    con = sqlite3.connect(path)
    try:
        pd.DataFrame([row]).to_sql("run_log", con, if_exists="append", index=False)
        con.commit()
    finally:
        con.close()


def query(path, sql, values=()):
    con = sqlite3.connect(path)
    try:
        return pd.read_sql_query(sql, con, params=values)
    finally:
        con.close()


def utc_now():
    return pd.Timestamp.now(tz="UTC").floor("s")


def text_time(t):
    # the way readings store their time, so text comparison in SQL works
    return t.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%S+00:00")


def newest_reading(live_db):
    if not Path(live_db).exists():
        return None
    try:
        value = query(live_db, "SELECT MAX(timestamp) AS t FROM readings")["t"].iloc[0]
    except Exception:
        # table does not exist before the first reading arrives
        return None
    return None if value is None or pd.isna(value) else pd.to_datetime(value, utc=True)


def clock_mode(live_db):
    # the replay plays old readings, so its "now" is the newest reading.
    # Everything else follows the real clock.
    try:
        row = query(live_db, "SELECT value FROM meta WHERE key = 'clock'")
    except Exception:
        return "real"
    return "real" if row.empty else row["value"].iloc[0]


def live_now(live_db):
    # None until the first reading has arrived
    newest = newest_reading(live_db)
    if newest is None or clock_mode(live_db) == "data":
        return newest
    return utc_now()


def read_live(live_db, since, until=None):
    sql = "SELECT * FROM readings WHERE timestamp >= ?"
    values = [text_time(since)]
    if until is not None:
        sql += " AND timestamp < ?"
        values.append(text_time(until))
    return query(live_db, sql, values)


def read_or_empty(path, sql, columns, values=()):
    # for tables that only exist once the collector has run
    try:
        return query(path, sql, values)
    except Exception:
        return pd.DataFrame(columns=columns)


def read_heartbeat(live_db, since, until=None):
    sql = "SELECT timestamp, network_ok FROM heartbeat WHERE timestamp >= ?"
    values = [text_time(since)]
    if until is not None:
        sql += " AND timestamp < ?"
        values.append(text_time(until))
    return read_or_empty(live_db, sql, ["timestamp", "network_ok"], values)


def read_machines(live_db):
    return list(read_or_empty(live_db, "SELECT code FROM machines", ["code"])["code"])


def get_state(path, key):
    row = read_or_empty(path, "SELECT value FROM run_state WHERE key = ?", ["value"], (key,))
    return None if row.empty else row["value"].iloc[0]


def set_state(path, key, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS run_state (key TEXT PRIMARY KEY, value TEXT)")
        con.execute("INSERT OR REPLACE INTO run_state VALUES (?, ?)", (key, value))
        con.commit()
    finally:
        con.close()


ROLLUP_COLUMNS = ["work_date", "machine", "failures", "run_s", "alarm_s", "cut_first_min", "cut_last_min"]


def load_rollup(path, before, days):
    # one row per machine per finished day, for the numbers that need more than today
    first = (pd.Timestamp(before) - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
    return read_or_empty(
        path,
        f"SELECT {', '.join(ROLLUP_COLUMNS)} FROM daily_rollup WHERE work_date < ? AND work_date >= ?",
        ROLLUP_COLUMNS,
        (before, first),
    )


def save_rollup(path, rows):
    con = sqlite3.connect(path)
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS daily_rollup (
                work_date TEXT, machine TEXT, failures INTEGER, run_s REAL, alarm_s REAL,
                cut_first_min REAL, cut_last_min REAL,
                PRIMARY KEY (work_date, machine)
            )""")
        marks = ", ".join("?" for _ in ROLLUP_COLUMNS)
        con.executemany(
            f"INSERT OR REPLACE INTO daily_rollup ({', '.join(ROLLUP_COLUMNS)}) VALUES ({marks})",
            clean(rows[ROLLUP_COLUMNS]).values.tolist(),
        )
        con.commit()
    finally:
        con.close()
