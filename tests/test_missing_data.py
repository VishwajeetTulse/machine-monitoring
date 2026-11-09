# a machine that cannot report something must give "no data", never a wrong number

import ctypes

import pandas as pd
import pytest

from cycle_drift import drift
from helpers import idle, readings, run, together
from oee import oee
from part_counting import to_parts
from performance import performance
from readings import blank_unsupported, check_capabilities, unsupported_columns
from shift_report import pct
from state_events import mark_setup, to_events
from test_classification import classify_frame


def events_of(df):
    df = df.copy()
    df["state"] = classify_frame(df)
    return mark_setup(to_events(df), setup_min_seconds=300)


def twelve_parts(machine="M1", **kwargs):
    return readings(machine, [run(60, finished=1, **kwargs) for _ in range(12)])


def test_no_cycle_time_gives_no_data_not_ok():
    df = twelve_parts()
    df["last_cycle_ms"] = None
    table = drift(to_parts(df), window=10, watch_pct=5, act_pct=10)
    assert table.loc[0, "status"] == "NO DATA"
    assert pd.isna(table.loc[0, "now_pct"])


def test_a_known_slowdown_still_reports():
    df = twelve_parts(ideal_s=50, cycle_s=60)
    assert drift(to_parts(df), window=10, watch_pct=5, act_pct=10).loc[0, "status"] == "ACT"


def test_a_program_with_no_ideal_time_blanks_performance_and_names_it():
    df = readings("M1", [run(60, finished=1, ideal_s=60, program=100),
                         run(60, finished=1, ideal_s=None, program=200)])
    p = performance(events_of(df), to_parts(df))
    assert pd.isna(p.loc["M1", "performance_pct"])
    assert p.loc["M1", "note"] == "ideal time missing for program 200"


def test_oee_is_blank_with_a_note_and_leaves_the_shop_number_alone():
    good = readings("M1", [run(30, finished=1, ideal_s=15)])
    bad = readings("M2", [run(30, finished=1, ideal_s=None, program=200)])
    df = together(good, bad)
    table = oee(events_of(df), to_parts(df), setup_is_planned=False)
    assert pd.isna(table.loc["M2", "oee_pct"])
    assert table.loc["M2", "availability_pct"] == 100.0
    assert "ideal time missing for program 200" in table.loc["M2", "note"]
    # only M1 can be judged: 15 of 30 s
    assert table.loc["ALL", "performance_pct"] == 50.0


def test_no_part_counter_gives_no_part_data():
    df = readings("M1", [run(60, finished=1), idle(10)])
    df["parts_today"] = None
    df["parts_life"] = None
    parts = to_parts(df)
    assert parts.empty
    table = oee(events_of(df), parts, setup_is_planned=False)
    assert pd.isna(table.loc["M1", "oee_pct"])
    assert "no part data" in table.loc["M1", "note"]


def test_unsupported_columns_are_blanked_not_zeroed():
    columns = unsupported_columns({"part_count": False, "cycle_time": True})
    assert columns == ["parts_today", "parts_life"]
    row = blank_unsupported({"parts_today": 5, "parts_life": 9, "last_cycle_ms": 1000}, columns)
    assert row == {"parts_today": None, "parts_life": None, "last_cycle_ms": 1000}


def test_a_capability_that_is_not_mentioned_counts_as_available():
    assert unsupported_columns({}) == []


@pytest.mark.parametrize("bad", [{"part_cout": True}, {"part_count": "no"}])
def test_a_wrong_capability_stops_the_collector(bad):
    with pytest.raises(ValueError):
        check_capabilities("M1", bad)


def test_the_focas_reader_never_asks_for_an_unsupported_value(monkeypatch):
    import readers
    monkeypatch.setattr(ctypes, "WinDLL", lambda path: object(), raising=False)
    machine = {"code": "M1", "ip": "10.0.0.1", "capabilities": {"part_count": False}}
    assert "parts_today" not in readers.FocasReader(machine).params
    assert "parts_today" in readers.FocasReader({"code": "M2", "ip": "10.0.0.2"}).params


def test_the_report_says_not_available_instead_of_none():
    assert pct(None) == "not available"
    assert pct(float("nan")) == "not available"
    assert pct(61.5) == "61.5%"
