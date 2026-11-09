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


def query(path, sql):
    con = sqlite3.connect(path)
    try:
        return pd.read_sql_query(sql, con)
    finally:
        con.close()