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


def save_tables(path, tables):
    # tables = {"name": DataFrame}
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(path)
    try:
        for name, df in tables.items():
            # replace = a new run never adds on top of an old one
            clean(df).to_sql(name, con, if_exists="replace", index=False)
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