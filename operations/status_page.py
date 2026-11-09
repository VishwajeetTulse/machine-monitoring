# live status page: shows every machine, today's numbers and open alerts
# the operator can also tap the reason of a stop on the machine card
# it reads the two databases. The only thing it writes is the reasons table.

import argparse
import sqlite3

import pandas as pd
from flask import Flask, jsonify, request

import reasons
from settings import load_settings

RESULTS_DB = "output/results.db"
LIVE_DB = "output/live.db"

app = Flask(__name__)
SETTINGS = {}


def read(db, sql):
    # read-only connection, so the page can never change the data
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    except sqlite3.OperationalError:
        return pd.DataFrame()
    try:
        return pd.read_sql_query(sql, con)
    except Exception:
        # table not created yet
        return pd.DataFrame()
    finally:
        con.close()


def local(text, zone):
    if text is None or pd.isna(text):
        return ""
    return pd.to_datetime(text, utc=True).tz_convert(zone).strftime("%H:%M")


def number(x, digits=1):
    return None if x is None or pd.isna(x) else round(float(x), digits)


def build():
    zone = SETTINGS["timezone"]
    silent_min = SETTINGS["alerts"]["silent_minutes"]

    last = read(LIVE_DB, "SELECT machine_code AS machine, MAX(timestamp) AS last FROM readings GROUP BY machine_code")
    events = read(RESULTS_DB, "SELECT * FROM events")
    oee = read(RESULTS_DB, "SELECT * FROM oee")
    parts = read(RESULTS_DB, "SELECT machine, SUM(parts_added) AS parts FROM parts GROUP BY machine")
    alerts = read(RESULTS_DB, "SELECT * FROM alerts")

    out = {"client": SETTINGS["client"], "time": "", "machines": [], "shop": None,
           "open": [], "recent": [], "choices": SETTINGS["stop_reasons"]}
    if last.empty:
        return out

    last["last"] = pd.to_datetime(last["last"], utc=True)
    now = last["last"].max()
    out["time"] = now.tz_convert(zone).strftime("%H:%M:%S")

    latest = {}
    if not events.empty:
        newest = events.sort_values(["machine", "started_at"]).groupby("machine").tail(1)
        latest = {r["machine"]: r for _, r in newest.iterrows()}
    oee_rows = {r["machine"]: r for _, r in oee.iterrows()} if not oee.empty else {}
    parts_by = dict(zip(parts["machine"], parts["parts"])) if not parts.empty else {}

    taps = reasons.load(LIVE_DB)
    taps["at"] = pd.to_datetime(taps["at"], utc=True)

    for _, row in last.sort_values("machine").iterrows():
        m = row["machine"]
        silent = (now - row["last"]).total_seconds() / 60 >= silent_min
        e = latest.get(m)
        state = "SILENT" if silent else (e["state"] if e is not None else "NO_DATA")
        since = None
        if e is not None:
            since = (now - pd.to_datetime(e["started_at"], utc=True)).total_seconds() / 60
        o = oee_rows.get(m)

        reason = None if e is None or pd.isna(e["reason"]) else e["reason"]
        # a tap made since this stop began shows at once, before the next calculation
        if e is not None:
            mine = taps[(taps["machine"] == m) & (taps["at"] >= pd.to_datetime(e["started_at"], utc=True))]
            if not mine.empty:
                reason = mine.iloc[-1]["reason"]

        out["machines"].append({
            "machine": m,
            "state": state,
            "can_pick": state in reasons.STOP_STATES,
            "stop_at": None if e is None else e["ended_at"],
            "reason": reason,
            "program": None if e is None or pd.isna(e["program_number"]) else int(e["program_number"]),
            "since_min": number(since, 0) if not silent else number((now - row["last"]).total_seconds() / 60, 0),
            "last_reading": local(row["last"].isoformat(), zone),
            "parts": int(parts_by.get(m, 0)),
            "availability": None if o is None else number(o["availability_pct"]),
            "performance": None if o is None else number(o["performance_pct"]),
            "oee": None if o is None else number(o["oee_pct"]),
        })

    if "ALL" in oee_rows:
        o = oee_rows["ALL"]
        out["shop"] = {
            "oee": number(o["oee_pct"]),
            "availability": number(o["availability_pct"]),
            "performance": number(o["performance_pct"]),
            "parts": int(sum(parts_by.values())),
        }

    if not alerts.empty:
        rows = []
        for _, a in alerts.iterrows():
            rows.append({
                "status": a["status"], "rule": a["rule"], "machine": a["machine"],
                "message": a["message"], "opened": local(a["opened_at"], zone),
                "cleared": local(a["cleared_at"], zone), "sort": a["opened_at"],
            })
        rows.sort(key=lambda r: r["sort"], reverse=True)
        out["open"] = [r for r in rows if r["status"] == "OPEN"]
        out["recent"] = [r for r in rows if r["status"] != "OPEN"][:8]
    return out


@app.route("/api/status")
def api_status():
    return jsonify(build())


@app.route("/api/reason", methods=["POST"])
def api_reason():
    body = request.get_json(silent=True) or {}
    machine = body.get("machine")
    reason = body.get("reason")

    if reason not in SETTINGS["stop_reasons"]:
        return jsonify({"error": "unknown reason"}), 400

    # only a machine that is stopped right now can be given a reason
    current = {m["machine"]: m for m in build()["machines"]}
    if machine not in current or not current[machine]["can_pick"]:
        return jsonify({"error": "machine is not stopped"}), 400

    # the tap belongs to the stop the operator sees, so it is stamped inside that stop
    reasons.add_reason(machine, current[machine]["stop_at"], reason, LIVE_DB)
    return jsonify({"ok": True})


@app.route("/")
def home():
    return PAGE


PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Machine status</title>
<style>
  body { font-family: Arial, sans-serif; margin: 0; background: #f2f4f7; color: #1d2733; }
  header { background: #1d2733; color: #fff; padding: 14px 20px; display: flex; justify-content: space-between; }
  header h1 { margin: 0; font-size: 20px; }
  main { padding: 20px; max-width: 1100px; margin: auto; }
  h2 { font-size: 16px; margin: 24px 0 10px; }
  .shop { display: flex; gap: 12px; flex-wrap: wrap; }
  .box { background: #fff; border-radius: 6px; padding: 12px 16px; min-width: 130px; }
  .box b { display: block; font-size: 24px; }
  .box span { font-size: 12px; color: #667; }
  .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 12px; }
  .card { background: #fff; border-radius: 6px; padding: 14px; border-top: 6px solid #999; }
  .card h3 { margin: 0 0 4px; font-size: 18px; }
  .state { font-weight: bold; }
  .small { font-size: 13px; color: #556; margin: 3px 0; }
  .RUNNING { border-top-color: #2e9e4f; } .RUNNING .state { color: #2e9e4f; }
  .IDLE { border-top-color: #e0a800; } .IDLE .state { color: #b38600; }
  .SETUP { border-top-color: #3b82c4; } .SETUP .state { color: #3b82c4; }
  .ALARM { border-top-color: #d63b3b; } .ALARM .state { color: #d63b3b; }
  .SILENT, .OFF, .NO_DATA { border-top-color: #888; } .SILENT .state, .OFF .state, .NO_DATA .state { color: #666; }
  table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 6px; }
  td, th { text-align: left; padding: 8px 10px; border-bottom: 1px solid #e6e9ee; font-size: 14px; }
  th { background: #e9edf2; }
  .none { color: #667; font-size: 14px; }
  .stop { background: #fff; border-radius: 6px; padding: 10px 14px; margin-bottom: 8px;
          display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
  .stop .who { min-width: 150px; }
  .stop .who b { font-size: 16px; }
  .stop .who span { display: block; font-size: 12px; color: #667; }
  .stop button { border: 1px solid #b9c2cc; background: #fff; border-radius: 14px; padding: 6px 12px;
                 margin: 2px 4px 2px 0; font-size: 13px; cursor: pointer; }
  .stop button.on { background: #1d2733; color: #fff; border-color: #1d2733; }
</style>
</head>
<body>
<header><h1 id="client">Machine status</h1><div id="time">waiting for data...</div></header>
<main>
  <div class="shop" id="shop"></div>
  <h2>Machines</h2>
  <div class="cards" id="cards"></div>
  <h2>Stopped now: tap the reason</h2>
  <div id="stops"></div>
  <h2>Open alerts</h2>
  <div id="open"></div>
  <h2>Recently cleared</h2>
  <div id="recent"></div>
</main>
<script>
function show(v, unit) { return v === null || v === undefined ? "-" : v + (unit || ""); }

function table(rows, cleared) {
  if (!rows.length) return '<div class="none">none</div>';
  var h = "<table><tr><th>Opened</th>" + (cleared ? "<th>Cleared</th>" : "") + "<th>Machine</th><th>Alert</th></tr>";
  rows.forEach(function (r) {
    h += "<tr><td>" + r.opened + "</td>" + (cleared ? "<td>" + r.cleared + "</td>" : "") +
         "<td>" + r.machine + "</td><td>" + r.message + "</td></tr>";
  });
  return h + "</table>";
}

function stops(d) {
  var h = "";
  d.machines.forEach(function (m) {
    if (!m.can_pick) return;
    h += '<div class="stop"><div class="who"><b>' + m.machine + '</b><span>' + m.state +
         ' for ' + show(m.since_min, " min") + '</span></div><div>';
    d.choices.forEach(function (c) {
      h += '<button data-machine="' + m.machine + '" data-reason="' + c + '"' +
           (c === m.reason ? ' class="on"' : '') + '>' + c.replace(/_/g, " ") + '</button>';
    });
    h += "</div></div>";
  });
  return h || '<div class="none">no machine is stopped</div>';
}

document.addEventListener("click", function (ev) {
  var b = ev.target.closest("button[data-reason]");
  if (!b) return;
  fetch("/api/reason", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({machine: b.dataset.machine, reason: b.dataset.reason})
  }).then(load);
});

function draw(d) {
  document.getElementById("client").textContent = d.client + " - machine status";
  document.getElementById("time").textContent = d.time ? "data time " + d.time : "waiting for data...";

  var s = d.shop;
  document.getElementById("shop").innerHTML = s ?
    '<div class="box"><b>' + show(s.oee, "%") + '</b><span>Shop OEE</span></div>' +
    '<div class="box"><b>' + show(s.availability, "%") + '</b><span>Availability</span></div>' +
    '<div class="box"><b>' + show(s.performance, "%") + '</b><span>Performance</span></div>' +
    '<div class="box"><b>' + s.parts + '</b><span>Parts</span></div>' : "";

  var c = "";
  d.machines.forEach(function (m) {
    var note = m.state === "SILENT" ? "no readings since " + m.last_reading :
               "for " + show(m.since_min, " min") + (m.reason ? " (" + m.reason + ")" : "");
    c += '<div class="card ' + m.state + '"><h3>' + m.machine + '</h3>' +
         '<div class="state">' + m.state + '</div>' +
         '<div class="small">' + note + '</div>' +
         '<div class="small">Program: ' + show(m.program) + '</div>' +
         '<div class="small">Parts: ' + m.parts + '</div>' +
         '<div class="small">OEE ' + show(m.oee, "%") + ' | Avail ' + show(m.availability, "%") +
         ' | Perf ' + show(m.performance, "%") + '</div></div>';
  });
  document.getElementById("cards").innerHTML = c || '<div class="none">no data yet</div>';

  document.getElementById("stops").innerHTML = stops(d);
  document.getElementById("open").innerHTML = table(d.open, false);
  document.getElementById("recent").innerHTML = table(d.recent, true);
}

function load() {
  fetch("/api/status").then(function (r) { return r.json(); }).then(draw).catch(function () {
    document.getElementById("time").textContent = "server not reachable";
  });
}
load();
setInterval(load, 5000);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--settings", default="settings/example_client.json")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to open from other PCs")
    a = p.parse_args()
    SETTINGS = load_settings(a.settings)
    print(f"status page on http://localhost:{a.port}  (Ctrl+C to stop)")
    app.run(host=a.host, port=a.port)