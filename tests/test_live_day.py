# the live path end to end: only today counts, and the real clock decides "now"

import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

import alerts
import run_all
import shift_report
import status_page
import store
from helpers import idle, readings, run, silent, stamp, together
from readings import open_db, register_machines, write, write_heartbeat
from store import clock_mode, live_now, query

IST = "Asia/Kolkata"


def at(text):
    return pd.Timestamp(text, tz=IST).tz_convert("UTC")


@pytest.fixture
def shop(tmp_path, monkeypatch):
    # every run works in its own folder, on its own databases
    monkeypatch.chdir(tmp_path)
    settings = {
        "client": "Test", "timezone": IST, "setup_is_planned": False,
        "shifts": [{"name": "Shift 1", "start": "07:00", "end": "15:00",
                    "breaks": [{"start": "12:30", "end": "13:00"}]}],
        "maintenance_jobs": [{"job": "Grease", "every_h": 100}],
    }
    path = tmp_path / "client.json"
    path.write_text(json.dumps(settings))
    return {"path": str(path), "settings": run_all.load_settings(str(path))}


def fill(df, heartbeat_from=None, heartbeat_to=None, machines=("M1",)):
    Path("output").mkdir(exist_ok=True)
    con = open_db("output/live.db")
    register_machines(con, [{"code": m} for m in machines])
    write(con, df.where(df.notna(), None).to_dict("records"))
    if heartbeat_from is not None:
        for t in pd.date_range(heartbeat_from, heartbeat_to, freq="5s"):
            write_heartbeat(con, True, stamp(t))
    con.close()


def freeze(monkeypatch, when):
    # the real clock, stopped at a chosen moment
    monkeypatch.setattr(store, "utc_now", lambda: when)


def use_replay_clock():
    con = sqlite3.connect("output/live.db")
    con.execute("INSERT OR REPLACE INTO meta VALUES ('clock', 'data')")
    con.commit()
    con.close()


MON = at("2026-11-02 07:00")
TUE = at("2026-11-03 07:00")


def two_parts(start, machine="M1"):
    return readings(machine, [run(60, finished=1), run(60, finished=1)], start=start)


def test_parts_today_are_only_todays(shop, monkeypatch):
    fill(together(two_parts(MON), two_parts(TUE)))
    freeze(monkeypatch, TUE + pd.Timedelta(minutes=2))
    tables = run_all.run(shop["path"], "unused", source="live")
    assert int(tables["parts"]["parts_added"].sum()) == 2


def test_yesterdays_readings_are_kept_in_the_database(shop, monkeypatch):
    fill(together(two_parts(MON), two_parts(TUE)))
    freeze(monkeypatch, TUE + pd.Timedelta(minutes=2))
    run_all.run(shop["path"], "unused", source="live")
    assert query("output/live.db", "SELECT COUNT(*) AS n FROM readings")["n"][0] == 240


def test_a_night_shift_stays_one_working_day(shop, monkeypatch):
    # 23:00 and 01:00 belong to the working day that began at 07:00
    two_hours_later = 2 * 3600 - 120
    fill(readings("M1", [run(60, finished=1), run(60, finished=1), silent(two_hours_later),
                         run(60, finished=1), run(60, finished=1)], start=at("2026-11-02 23:00")))
    freeze(monkeypatch, at("2026-11-03 01:05"))
    tables = run_all.run(shop["path"], "unused", source="live")
    assert int(tables["parts"]["parts_added"].sum()) == 4


def test_the_board_clock_is_real_after_the_collector_dies(shop, monkeypatch):
    fill(two_parts(MON))
    freeze(monkeypatch, MON + pd.Timedelta(minutes=30))
    assert live_now("output/live.db") == MON + pd.Timedelta(minutes=30)


def test_the_replay_keeps_its_own_clock(shop, monkeypatch):
    fill(two_parts(MON))
    use_replay_clock()
    freeze(monkeypatch, MON + pd.Timedelta(days=30))
    assert clock_mode("output/live.db") == "data"
    assert live_now("output/live.db") == MON + pd.Timedelta(seconds=119)


def test_a_dead_collector_raises_a_silent_alert(shop, monkeypatch):
    fill(two_parts(MON))
    freeze(monkeypatch, MON + pd.Timedelta(minutes=30))
    run_all.run(shop["path"], "unused", source="live")
    opened, _ = alerts.check(shop["settings"], now=MON + pd.Timedelta(minutes=30))
    assert [c["rule"] for c in opened] == ["silent"]


def test_no_silent_alert_outside_working_time(shop, monkeypatch):
    fill(two_parts(MON))
    night = at("2026-11-02 20:00")
    freeze(monkeypatch, night)
    run_all.run(shop["path"], "unused", source="live")
    opened, _ = alerts.check(shop["settings"], now=night)
    assert "silent" not in [c["rule"] for c in opened]


def test_no_silent_alert_during_a_break(shop, monkeypatch):
    fill(two_parts(MON))
    lunch = at("2026-11-02 12:45")
    freeze(monkeypatch, lunch)
    run_all.run(shop["path"], "unused", source="live")
    opened, _ = alerts.check(shop["settings"], now=lunch)
    assert "silent" not in [c["rule"] for c in opened]


def test_silence_alert_still_works_with_no_results_today(shop, monkeypatch):
    fill(two_parts(MON))
    morning = TUE + pd.Timedelta(minutes=10)
    freeze(monkeypatch, morning)
    with pytest.raises(run_all.NoReadings):
        run_all.run(shop["path"], "unused", source="live")
    opened, _ = alerts.check(shop["settings"], now=morning, silent_only=True)
    assert [c["rule"] for c in opened] == ["silent"]


def test_a_finished_shift_gets_its_report_when_the_machines_stop_early(shop, monkeypatch):
    # readings end at 07:02, long before 15:00. The real clock still closes the shift.
    fill(two_parts(MON))
    after = at("2026-11-02 15:00:05")
    freeze(monkeypatch, after)
    run_all.run(shop["path"], "unused", source="live")
    written = shift_report.check(shop["settings"], now=after)
    assert len(written) == 1
    assert "Parts made: 2" in Path(written[0]["file"]).read_text(encoding="utf-8")


def test_no_report_before_the_shift_ends(shop, monkeypatch):
    fill(two_parts(MON))
    during = at("2026-11-02 14:00")
    freeze(monkeypatch, during)
    run_all.run(shop["path"], "unused", source="live")
    assert shift_report.check(shop["settings"], now=during) == []


def test_the_last_shift_of_a_day_is_reported_when_the_next_day_begins(shop, monkeypatch):
    # three shifts: the last one ends exactly as the next working day begins
    settings = json.load(open(shop["path"]))
    settings["shifts"] = [
        {"name": "A", "start": "07:00", "end": "15:00", "breaks": []},
        {"name": "B", "start": "15:00", "end": "23:00", "breaks": []},
        {"name": "C", "start": "23:00", "end": "07:00", "breaks": []},
    ]
    Path(shop["path"]).write_text(json.dumps(settings))
    fill(two_parts(at("2026-11-03 05:00")))

    freeze(monkeypatch, at("2026-11-03 05:10"))
    run_all.run(shop["path"], "unused", source="live")

    # the box is off at 07:00 and returns at 07:30, with new readings
    fill(two_parts(at("2026-11-03 07:25")))
    freeze(monkeypatch, at("2026-11-03 07:30"))
    run_all.run(shop["path"], "unused", source="live")

    reports = query("output/results.db", "SELECT shift, date FROM shift_reports")
    assert ("C", "2026-11-02") in set(zip(reports["shift"], reports["date"]))


def test_a_report_is_written_only_once(shop, monkeypatch):
    fill(two_parts(MON))
    after = at("2026-11-02 15:00:05")
    freeze(monkeypatch, after)
    run_all.run(shop["path"], "unused", source="live")
    assert len(shift_report.check(shop["settings"], now=after)) == 1
    assert shift_report.check(shop["settings"], now=after) == []


def test_mtbf_adds_up_across_days(shop, monkeypatch):
    # one failure a day, for 2 days: 120 s of run time and 2 failures in total
    from helpers import alarm
    day1 = readings("M1", [run(60), alarm(60)], start=MON)
    day2 = readings("M1", [run(60), alarm(60)], start=TUE)
    fill(day1)
    freeze(monkeypatch, MON + pd.Timedelta(minutes=3))
    run_all.run(shop["path"], "unused", source="live")

    fill(day2)
    freeze(monkeypatch, TUE + pd.Timedelta(minutes=3))
    tables = run_all.run(shop["path"], "unused", source="live")

    row = tables["mtbf_mttr"].set_index("machine").loc["M1"]
    assert row["failures"] == 2
    assert row["run_min"] == 2.0
    assert row["mtbf_min"] == 1.0


def test_the_maintenance_rate_comes_from_finished_days(shop, monkeypatch):
    def cutting(start, first, last):
        df = readings("M1", [run(10)], start=start)
        df["cutting_min"] = first
        df.loc[df.index[-1], "cutting_min"] = last
        return df

    fill(cutting(MON, 600, 900))           # 5 hours on Monday
    freeze(monkeypatch, MON + pd.Timedelta(minutes=1))
    run_all.run(shop["path"], "unused", source="live")

    fill(cutting(TUE, 900, 905))           # Tuesday has only just begun
    freeze(monkeypatch, TUE + pd.Timedelta(minutes=1))
    tables = run_all.run(shop["path"], "unused", source="live")

    row = tables["maintenance"].iloc[0]
    # the job was never done on record, so check the rate through the table used
    from maintenance import hours_from_rollup
    from store import load_rollup
    rollup = pd.concat([load_rollup("output/results.db", "2026-11-04", 30)])
    hours = hours_from_rollup(rollup)
    assert hours.loc["M1", "per_day_h"] == 5.0
    assert round(hours.loc["M1", "now_h"], 2) == round(905 / 60, 2)
    assert row["status"] == "NO RECORD"


def test_a_machine_with_no_readings_today_still_appears(shop, monkeypatch):
    fill(two_parts(MON), MON, MON + pd.Timedelta(minutes=10), machines=("M1", "M2"))
    freeze(monkeypatch, MON + pd.Timedelta(minutes=10))
    tables = run_all.run(shop["path"], "unused", source="live")
    row = tables["oee"].set_index("machine").loc["M2"]
    assert row["availability_pct"] == 0.0
    assert row["note"] == "no part data"


# ---- the board

@pytest.fixture
def board(shop):
    status_page.SETTINGS = shop["settings"]
    return status_page


def test_the_board_says_no_data_when_everything_stops(shop, board, monkeypatch):
    fill(two_parts(MON))
    late = MON + pd.Timedelta(minutes=30)
    freeze(monkeypatch, late)
    run_all.run(shop["path"], "unused", source="live")
    data = board.build()
    assert data["no_data_since"] == "07:01"
    assert data["machines"][0]["state"] == "SILENT"


def test_the_board_stays_live_while_readings_arrive(shop, board, monkeypatch):
    fill(two_parts(MON))
    freeze(monkeypatch, MON + pd.Timedelta(seconds=125))
    run_all.run(shop["path"], "unused", source="live")
    assert board.build()["no_data_since"] is None


def test_the_board_does_not_show_yesterdays_numbers_as_today(shop, board, monkeypatch):
    fill(two_parts(MON))
    freeze(monkeypatch, MON + pd.Timedelta(minutes=3))
    run_all.run(shop["path"], "unused", source="live")
    freeze(monkeypatch, TUE + pd.Timedelta(minutes=10))
    data = board.build()
    assert data["machines"][0]["parts"] == 0
    assert data["shop"] is None


def test_an_operator_tap_reaches_the_stop(shop, board, monkeypatch):
    fill(readings("M1", [run(30), idle(90)], start=MON))
    freeze(monkeypatch, MON + pd.Timedelta(seconds=120))
    run_all.run(shop["path"], "unused", source="live")

    client = board.app.test_client()
    answer = client.post("/api/reason", json={"machine": "M1", "reason": "NO_MATERIAL"})
    assert answer.status_code == 200

    tables = run_all.run(shop["path"], "unused", source="live")
    assert "NO_MATERIAL" in set(tables["downtime"]["reason"])
    assert board.build()["machines"][0]["set_by"] == "operator"


def test_an_unknown_reason_is_refused(shop, board, monkeypatch):
    fill(readings("M1", [run(30), idle(90)], start=MON))
    freeze(monkeypatch, MON + pd.Timedelta(seconds=120))
    run_all.run(shop["path"], "unused", source="live")
    answer = board.app.test_client().post("/api/reason", json={"machine": "M1", "reason": "MADE_UP"})
    assert answer.status_code == 400


def test_the_page_and_the_status_api_answer(shop, board, monkeypatch):
    fill(two_parts(MON))
    freeze(monkeypatch, MON + pd.Timedelta(seconds=125))
    run_all.run(shop["path"], "unused", source="live")
    client = board.app.test_client()
    assert client.get("/").status_code == 200
    assert client.get("/api/status").get_json()["machines"][0]["machine"] == "M1"
