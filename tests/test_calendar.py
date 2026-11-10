# breaks, silent machines and the working day

import json

import pandas as pd
import pytest

from availability import availability
from downtime import downtime_by_reason
from gaps import coverage, fill_gaps, healthy_windows
from helpers import START, alarm, idle, off, readings, run, silent
from settings import load_settings
from shift_calendar import apply_calendar, is_working, pieces, working_day_start, working_windows
from state_events import mark_setup, to_events
from test_classification import ALARM, IDLE, NO_DATA, NOT_RESPONDING, PLANNED_STOP, RUNNING, classify_frame

IST = "Asia/Kolkata"


def settings_with(breaks=(), start="07:00", end="15:00", weekly_off=()):
    return {
        "timezone": IST,
        "weekly_off": list(weekly_off),
        "shifts": [{"name": "Shift 1", "start": start, "end": end, "breaks": list(breaks)}],
    }


def at(text):
    # "2026-11-02 09:00" in IST
    return pd.Timestamp(text, tz=IST).tz_convert("UTC")


def events_of(df, **gap_options):
    df = df.copy()
    df["state"] = classify_frame(df)
    return mark_setup(to_events(fill_gaps(df, 5, **gap_options)), setup_min_seconds=300)


def by_state(events, machine="M1"):
    return events[events["machine"] == machine].groupby("state")["duration_s"].sum().to_dict()


# ---- working windows

def test_a_break_splits_the_shift():
    s = settings_with([{"start": "12:30", "end": "13:00"}])
    windows = working_windows(s, at("2026-11-02 08:00"), at("2026-11-02 08:00"))
    assert (at("2026-11-02 07:00"), at("2026-11-02 12:30")) in windows
    assert (at("2026-11-02 13:00"), at("2026-11-02 15:00")) in windows


def test_a_night_shift_crosses_midnight_with_its_break():
    s = settings_with([{"start": "01:00", "end": "01:30"}], start="22:00", end="06:00")
    windows = working_windows(s, at("2026-11-03 02:00"), at("2026-11-03 02:00"))
    assert (at("2026-11-02 22:00"), at("2026-11-03 01:00")) in windows
    assert (at("2026-11-03 01:30"), at("2026-11-03 06:00")) in windows


def test_shifts_that_follow_each_other_become_one_window():
    s = settings_with()
    s["shifts"].append({"name": "Shift 2", "start": "15:00", "end": "23:00", "breaks": []})
    windows = working_windows(s, at("2026-11-02 08:00"), at("2026-11-02 08:00"))
    assert (at("2026-11-02 07:00"), at("2026-11-02 23:00")) in windows


def test_the_weekly_off_has_no_working_time():
    # 8 Nov 2026 is a Sunday
    s = settings_with(weekly_off=["Sunday"])
    assert not is_working(at("2026-11-08 09:00"), s)
    assert is_working(at("2026-11-09 09:00"), s)


@pytest.mark.parametrize("when,expected", [
    ("2026-11-02 12:29:59", True), ("2026-11-02 12:30:00", False),
    ("2026-11-02 12:59:59", False), ("2026-11-02 13:00:00", True),
    ("2026-11-02 14:59:59", True), ("2026-11-02 15:00:00", False),
    ("2026-11-02 06:59:59", False),
])
def test_is_working_at_the_edges(when, expected):
    s = settings_with([{"start": "12:30", "end": "13:00"}])
    assert is_working(at(when), s) is expected


def test_pieces_cover_every_second_once():
    windows = [(START + pd.Timedelta(seconds=10), START + pd.Timedelta(seconds=20))]
    cut = pieces(START, START + pd.Timedelta(seconds=29), windows)
    assert [(int((b - a).total_seconds()) + 1, w) for a, b, w in cut] == [(10, False), (10, True), (10, False)]


# ---- the working day

@pytest.mark.parametrize("now,start", [
    ("2026-11-02 09:00", "2026-11-02 07:00"),
    ("2026-11-02 23:30", "2026-11-02 07:00"),
    ("2026-11-03 02:00", "2026-11-02 07:00"),   # still yesterday's working day
    ("2026-11-03 07:00", "2026-11-03 07:00"),
])
def test_the_working_day_starts_with_the_first_shift(now, start):
    assert working_day_start(at(now), settings_with()) == at(start)


def test_a_night_only_shop_stays_in_one_working_day():
    s = settings_with(start="22:00", end="06:00")
    assert working_day_start(at("2026-11-03 02:00"), s) == at("2026-11-02 22:00")


# ---- breaks

def test_lunch_with_the_power_on_is_not_a_loss():
    # 07:00-07:01:40 run, then the machine idles for 100 s, 60 of them in a break
    s = settings_with([{"start": "07:02", "end": "07:03"}])
    ev = apply_calendar(events_of(readings("M1", [run(100), idle(120), run(60)])), s)
    states = by_state(ev)
    assert states[IDLE] == 60
    assert states[PLANNED_STOP] == 60
    assert availability(ev, False).loc["M1", "availability_pct"] == round(160 / 220 * 100, 1)


def test_the_reason_stays_on_both_halves_of_a_cut_stop():
    s = settings_with([{"start": "07:02", "end": "07:03"}])
    ev = apply_calendar(events_of(readings("M1", [run(100), idle(120, "NO_MATERIAL")])), s)
    assert set(ev.loc[ev["state"] == IDLE, "reason"]) == {"NO_MATERIAL"}


def test_downtime_leaves_the_break_out():
    s = settings_with([{"start": "07:02", "end": "07:03"}])
    ev = apply_calendar(events_of(readings("M1", [run(100), idle(120, "NO_MATERIAL")])), s)
    assert downtime_by_reason(ev).loc["NO_MATERIAL", "minutes"] == 1.0


def test_an_alarm_across_a_break_is_still_one_failure():
    s = settings_with([{"start": "07:02", "end": "07:03"}])
    ev = apply_calendar(events_of(readings("M1", [run(100), alarm(120), run(10)])), s)
    assert len(ev[ev["state"] == ALARM]) == 1


def test_a_machine_running_through_a_break_keeps_its_run_time():
    s = settings_with([{"start": "07:00", "end": "07:01"}])
    ev = apply_calendar(events_of(readings("M1", [run(200)])), s)
    assert by_state(ev) == {RUNNING: 200}


def test_every_second_is_kept():
    s = settings_with([{"start": "07:02", "end": "07:03"}])
    df = readings("M1", [run(100), idle(120), run(60), off(100)])
    before = int(events_of(df)["duration_s"].sum())
    assert int(apply_calendar(events_of(df), s)["duration_s"].sum()) == before


def test_time_after_the_shift_is_a_planned_stop():
    s = settings_with(end="07:01")
    ev = apply_calendar(events_of(readings("M1", [idle(120)])), s)
    assert by_state(ev) == {IDLE: 60, PLANNED_STOP: 60}


# ---- silence

def beats(first, last, ok=1):
    times = pd.date_range(first, last, freq="5s")
    return pd.DataFrame({"timestamp": [t.isoformat() for t in times], "network_ok": ok})


def test_silence_with_a_healthy_box_is_lost_time():
    df = readings("M1", [run(100), silent(100), run(100)])
    healthy = healthy_windows(beats(START, START + pd.Timedelta(seconds=300)))
    ev = apply_calendar(events_of(df, healthy=healthy), settings_with())
    assert by_state(ev)[NOT_RESPONDING] == 100
    a = availability(ev, False)
    assert a.loc["M1", "planned_s"] == 300
    assert a.loc["M1", "availability_pct"] == round(200 / 300 * 100, 1)


def test_silence_with_a_dead_box_is_not_counted():
    df = readings("M1", [run(100), silent(100), run(100)])
    # no heartbeat at all during the silence
    healthy = healthy_windows(pd.concat([
        beats(START, START + pd.Timedelta(seconds=95)),
        beats(START + pd.Timedelta(seconds=205), START + pd.Timedelta(seconds=300)),
    ]))
    ev = apply_calendar(events_of(df, healthy=healthy), settings_with())
    assert NOT_RESPONDING not in by_state(ev) or by_state(ev)[NOT_RESPONDING] <= 10
    assert availability(ev, False).loc["M1", "planned_s"] <= 210


def test_silence_during_a_network_failure_is_not_counted():
    df = readings("M1", [run(100), silent(100), run(100)])
    heartbeat = beats(START, START + pd.Timedelta(seconds=300))
    heartbeat["network_ok"] = 0
    ev = apply_calendar(events_of(df, healthy=healthy_windows(heartbeat)), settings_with())
    assert by_state(ev)[NO_DATA] == 100
    assert availability(ev, False).loc["M1", "planned_s"] == 200


def test_without_heartbeats_every_gap_stays_no_data():
    ev = apply_calendar(events_of(readings("M1", [run(100), silent(100), run(100)])), settings_with())
    assert by_state(ev)[NO_DATA] == 100


def test_a_box_failure_in_the_middle_of_a_silence_is_split():
    df = readings("M1", [run(100), silent(100), run(100)])
    healthy = [(START + pd.Timedelta(seconds=100), START + pd.Timedelta(seconds=149))]
    ev = apply_calendar(events_of(df, healthy=healthy), settings_with())
    states = by_state(ev)
    assert states[NOT_RESPONDING] == 50
    assert states[NO_DATA] == 50


def test_silence_in_a_break_is_a_planned_stop():
    s = settings_with([{"start": "07:01", "end": "07:04"}])
    df = readings("M1", [run(60), silent(180), run(60)])
    healthy = healthy_windows(beats(START, START + pd.Timedelta(seconds=300)))
    ev = apply_calendar(events_of(df, healthy=healthy), s)
    assert by_state(ev) == {RUNNING: 120, PLANNED_STOP: 180}


def test_a_machine_that_goes_silent_and_stays_silent_is_counted():
    df = readings("M1", [run(100)])
    end = START + pd.Timedelta(seconds=199)
    healthy = healthy_windows(beats(START, end))
    ev = apply_calendar(events_of(df, healthy=healthy, start=START, end=end), settings_with())
    assert by_state(ev) == {RUNNING: 100, NOT_RESPONDING: 100}


def test_a_machine_that_starts_late_is_counted_from_the_shift_start():
    df = readings("M1", [run(100)], start=START + pd.Timedelta(seconds=100))
    end = START + pd.Timedelta(seconds=199)
    healthy = healthy_windows(beats(START, end))
    ev = apply_calendar(events_of(df, healthy=healthy, start=START, end=end), settings_with())
    assert by_state(ev) == {RUNNING: 100, NOT_RESPONDING: 100}


def test_a_machine_with_no_readings_at_all_still_appears():
    df = readings("M1", [run(100)])
    end = START + pd.Timedelta(seconds=99)
    healthy = healthy_windows(beats(START, end))
    ev = apply_calendar(events_of(df, healthy=healthy, start=START, end=end, machines=["M1", "M2"]), settings_with())
    assert by_state(ev, "M2") == {NOT_RESPONDING: 100}
    a = availability(ev, False)
    assert a.loc["M2", "availability_pct"] == 0.0


def test_not_responding_has_its_own_label_in_downtime():
    df = readings("M1", [run(100), silent(100), run(100)])
    healthy = healthy_windows(beats(START, START + pd.Timedelta(seconds=300)))
    table = downtime_by_reason(apply_calendar(events_of(df, healthy=healthy), settings_with()))
    assert table.loc["NOT_RESPONDING", "stops"] == 1


def test_off_during_working_time_is_lost_time():
    ev = apply_calendar(events_of(readings("M1", [run(100), off(100)])), settings_with())
    assert by_state(ev) == {RUNNING: 100, NOT_RESPONDING: 100}
    assert availability(ev, False).loc["M1", "availability_pct"] == 50.0


def test_coverage_counts_only_working_time():
    s = settings_with([{"start": "07:01", "end": "07:02"}])
    df = readings("M1", [run(60), idle(60), silent(30), run(30)])
    ev = apply_calendar(events_of(df), s)
    assert coverage(ev).loc["M1", "coverage_pct"] == round((1 - 30 / 120) * 100, 1)


# ---- settings

def write_settings(tmp_path, shifts, **extra):
    path = tmp_path / "client.json"
    path.write_text(json.dumps({"client": "X", "setup_is_planned": False, "shifts": shifts, **extra}))
    return path


GOOD = {"name": "S", "start": "07:00", "end": "15:00", "breaks": []}


def test_a_shift_must_say_its_breaks(tmp_path):
    shift = {k: v for k, v in GOOD.items() if k != "breaks"}
    with pytest.raises(ValueError, match="breaks"):
        load_settings(write_settings(tmp_path, [shift]))


def test_a_break_outside_the_shift_is_refused(tmp_path):
    shift = dict(GOOD, breaks=[{"start": "16:00", "end": "16:30"}])
    with pytest.raises(ValueError, match="outside"):
        load_settings(write_settings(tmp_path, [shift]))


def test_a_night_shift_break_after_midnight_is_accepted(tmp_path):
    shift = {"name": "N", "start": "22:00", "end": "06:00", "breaks": [{"start": "01:00", "end": "01:30"}]}
    assert load_settings(write_settings(tmp_path, [shift]))["shifts"][0]["name"] == "N"


def test_a_wrong_weekly_off_is_refused(tmp_path):
    with pytest.raises(ValueError, match="weekly_off"):
        load_settings(write_settings(tmp_path, [GOOD], weekly_off=["Sundy"]))
