import pandas as pd

from part_counting import to_parts


def load_rejects(path):

    r = pd.read_csv(path)

    if not {"machine", "rejects"}.issubset(r.columns):
        raise ValueError("rejects file needs columns: machine, rejects")
    if (r["rejects"] < 0).any():
        raise ValueError("rejects cannot be negative")

    return r


def quality(parts, rejects=None):

    table = pd.DataFrame({"parts": parts.groupby("machine")["parts_added"].sum()})

    if rejects is None:
        entered = pd.Series(dtype=float)
    else:
        entered = rejects.groupby("machine")["rejects"].sum()

    table["rejects"] = entered.reindex(table.index)
    table["good"] = table["parts"] - table["rejects"]

    known = table[table["rejects"].notna()]
    table.loc["ALL"] = [known["parts"].sum(), known["rejects"].sum(), known["good"].sum()]
    if known.empty:
        table.loc["ALL", ["parts", "rejects", "good"]] = [table["parts"].iloc[:-1].sum(), float("nan"), float("nan")]

    table["quality_pct"] = (
        table["good"] / table["parts"].where(table["parts"] > 0) * 100
    ).round(1)

    table["note"] = ""
    table.loc[table["rejects"].isna(), "note"] = "quality not measured"
    table.loc[table["rejects"] > table["parts"], "note"] = "rejects more than parts, check"

    return table


if __name__ == "__main__":

    df = pd.read_csv("data/cnc_sim_data.csv", low_memory=False)
    parts = to_parts(df)

    print("no rejects entered:")
    print(quality(parts).to_string())
    print()

    pd.DataFrame({"machine": ["V60", "V61", "V62"], "rejects": [0, 6, 1]}).to_csv(
        "data/rejects_example.csv", index=False
    )

    print("rejects from data/rejects_example.csv (made up):")
    print(quality(parts, load_rejects("data/rejects_example.csv")).to_string())