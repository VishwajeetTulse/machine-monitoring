# every expected number here was worked out by hand from the made-up day

import pandas as pd
import pytest

from availability import availability
from cycle_drift import drift
from downtime import downtime_by_reason
from gaps import find_gaps
from helpers import START, alarm, idle, off, readings, run, silent, stamp, together
from mtbf_mttr import mtbf_mttr
from oee import oee
from part_counting import to_parts
from performance import performance
from quality import quality
from state_events import mark_setup, to_events
from test_classification import ALARM, IDLE, OFF, RUNNING, SETUP, classify_frame


def classified(df):
    df = df.copy()
    df["state"] = classify_frame(df)
    return df


def events_of(df, setup_min=300):
    return mark_setup(to_events(classified(df)), setup_min_seconds=setup_min)


def test_each_reading_gets_the_right_state():
    df = readings("M1", [run(1), idle(1), off(1), alarm(1)])
    assert list(classify_frame(df)) == [RUNNING, IDLE, OFF, ALARM]


def test_an_alarm_wins_over_everything_else():
    df = readings("M1", [run(1)])
    df["alarm_code"] = 500
    assert list(classify_frame(df)) == [ALARM]


def test_readings_become_events_with_inclusive_durations():
    ev = events_of(readings("M1", [run(10), idle(5), run(20)]))
    assert list(ev["state"]) == [RUNNING, IDLE, RUNNING]
    assert list(ev["duration_s"]) == [10, 5, 20]


def test_two_machines_are_never_merged():
    ev = events_of(together(readings("M1", [run(10)]), readings("M2", [run(10)])))
    assert sorted(ev["machine"]) == ["M1", "M2"]
    assert list(ev["duration_s"]) == [10, 10]


def test_a_long_stop_with_a_new_program_is_setup():
    ev = events_of(readings("M1", [run(10, program=100), idle(300, program=200), run(10, program=200)]))
    assert list(ev["state"]) == [RUNNING, SETUP, RUNNING]


def test_a_short_stop_with_a_new_program_stays_idle():
    ev = events_of(readings("M1", [run(10, program=100), idle(299, program=200), run(10, program=200)]))
    assert list(ev["state"]) == [RUNNING, IDLE, RUNNING]


def test_a_long_stop_on_the_same_program_stays_idle():
    ev = events_of(readings("M1", [run(10), idle(400), run(10)]))
    assert list(ev["state"]) == [RUNNING, IDLE, RUNNING]


def test_parts_are_counted_from_the_counter():
    parts = to_parts(readings("M1", [run(60, finished=1), run(60, finished=1), run(60, finished=2)]))
    assert list(parts["parts_added"]) == [1, 1, 2]


def test_a_part_is_compared_with_its_ideal_time():
    parts = to_parts(readings("M1", [run(60, finished=1, ideal_s=50, cycle_s=60)]))
    assert parts.loc[0, "cycle_s"] == 60
    assert parts.loc[0, "ideal_s"] == 50
    assert parts.loc[0, "slower_pct"] == 20.0


def test_availability_leaves_off_time_out_of_planned_time():
    ev = events_of(readings("M1", [run(60), idle(20), off(20)]))
    a = availability(ev, setup_is_planned=False)
    assert a.loc["M1", "run_s"] == 60
    assert a.loc["M1", "planned_s"] == 80
    assert a.loc["M1", "availability_pct"] == 75.0


def test_planned_setup_leaves_planned_time():
    df = readings("M1", [run(100, program=100), idle(300, program=200), run(100, program=200)])
    ev = events_of(df)
    assert availability(ev, setup_is_planned=False).loc["M1", "planned_s"] == 500
    assert availability(ev, setup_is_planned=True).loc["M1", "planned_s"] == 200


def test_performance_is_ideal_time_over_run_time():
    df = readings("M1", [run(50, finished=1, ideal_s=30), run(50, finished=1, ideal_s=30)])
    p = performance(events_of(df), to_parts(df))
    assert p.loc["M1", "parts"] == 2
    assert p.loc["M1", "performance_pct"] == 60.0


def test_a_machine_with_no_parts_has_no_performance():
    df = together(readings("M1", [run(50, finished=1, ideal_s=30)]), readings("M2", [run(50)]))
    p = performance(events_of(df), to_parts(df))
    assert pd.isna(p.loc["M2", "performance_pct"])
    assert p.loc["M2", "note"] == "no part data"
    # the machine with no parts must not dilute the shop number
    assert p.loc["ALL", "performance_pct"] == 60.0


def test_quality_needs_entered_rejects():
    parts = to_parts(readings("M1", [run(10, finished=4)]))
    assert quality(parts).loc["M1", "note"] == "quality not measured"

    rejects = pd.DataFrame({"machine": ["M1"], "rejects": [1]})
    assert quality(parts, rejects).loc["M1", "quality_pct"] == 75.0


def test_oee_is_the_three_factors_multiplied():
    # run 100 of 125 planned = 80%. Ideal 60 of 100 run = 60%. 1 of 2 good = 50%.
    df = readings("M1", [run(50, finished=1, ideal_s=30), run(50, finished=1, ideal_s=30), idle(25)])
    rejects = pd.DataFrame({"machine": ["M1"], "rejects": [1]})
    table = oee(events_of(df), to_parts(df), setup_is_planned=False, rejects=rejects)
    assert table.loc["M1", "availability_pct"] == 80.0
    assert table.loc["M1", "performance_pct"] == 60.0
    assert table.loc["M1", "quality_pct"] == 50.0
    assert table.loc["M1", "oee_pct"] == 24.0


def test_downtime_is_grouped_by_reason_and_skips_off_time():
    df = readings("M1", [run(10), idle(30, "LOADING"), run(10), idle(30, "LOADING"), run(10),
                         idle(60, "NO_MATERIAL"), off(1000)])
    table = downtime_by_reason(events_of(df))
    assert list(table.index) == ["LOADING", "NO_MATERIAL"] or list(table.index) == ["NO_MATERIAL", "LOADING"]
    assert table.loc["LOADING", "stops"] == 2
    assert table.loc["LOADING", "minutes"] == 1.0
    assert table.loc["NO_MATERIAL", "minutes"] == 1.0
    assert table["share_pct"].sum() == 100.0


def test_a_stop_with_no_reason_counts_as_unknown():
    table = downtime_by_reason(events_of(readings("M1", [run(10), idle(30), run(10)])))
    assert table.loc["UNKNOWN", "stops"] == 1


def test_a_gap_is_found_with_its_missing_seconds():
    gaps = find_gaps(readings("M1", [run(10), silent(600), run(10)]), gap_seconds=5)
    assert len(gaps) == 1
    assert gaps.loc[0, "missing_s"] == 600
    assert gaps.loc[0, "gap_start"] == START + pd.Timedelta(seconds=10)
    assert gaps.loc[0, "gap_end"] == START + pd.Timedelta(seconds=609)


def test_a_short_gap_is_ignored():
    assert find_gaps(readings("M1", [run(10), silent(3), run(10)]), gap_seconds=5).empty


def test_mtbf_and_mttr_from_alarm_periods():
    # 200 s running and 2 alarms of 30 and 90 s: MTBF 100 s, MTTR 60 s
    df = readings("M1", [run(100), alarm(30), run(100), alarm(90)])
    table = mtbf_mttr(events_of(df))
    assert table.loc["M1", "failures"] == 2
    assert table.loc["M1", "mtbf_min"] == round(100 / 60, 1)
    assert table.loc["M1", "mttr_min"] == 1.0


def parts_with_slowdown(percentages, ideal_s=100):
    rows = []
    t = START
    for pct in percentages:
        t += pd.Timedelta(seconds=200)
        rows.append({"machine": "M1", "program_number": 100, "finished_at": t, "parts_added": 1,
                     "cycle_s": ideal_s * (1 + pct / 100), "ideal_s": ideal_s, "slower_pct": pct})
    return pd.DataFrame(rows)


@pytest.mark.parametrize("pct,status", [(0, "OK"), (5, "WATCH"), (9.9, "WATCH"), (10, "ACT"), (30, "ACT")])
def test_drift_status_follows_the_limits(pct, status):
    table = drift(parts_with_slowdown([pct] * 12), window=10, watch_pct=5, act_pct=10)
    assert table.loc[0, "status"] == status


def test_drift_needs_a_full_window():
    table = drift(parts_with_slowdown([50] * 9), window=10, watch_pct=5, act_pct=10)
    assert table.loc[0, "status"] == "TOO FEW PARTS"


def test_stamp_format_matches_the_simulator():
    assert stamp(START) == "2026-11-02T01:30:00+00:00"
