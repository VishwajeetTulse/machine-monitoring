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


def read(db, sql, values=()):
    # read-only connection, so the page can never change the data
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    except sqlite3.OperationalError:
        return pd.DataFrame()
    try:
        return pd.read_sql_query(sql, con, params=values)
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
    downtime = read(RESULTS_DB, "SELECT machine, reason, minutes FROM downtime")

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
        set_by = "machine" if reason else None
        set_at = None

        # a tap made since this stop began shows at once, before the next calculation
        if e is not None:
            mine = taps[(taps["machine"] == m) & (taps["at"] >= pd.to_datetime(e["started_at"], utc=True))]
            if not mine.empty:
                reason = mine.iloc[-1]["reason"]
                set_by = "operator"
                set_at = local(mine.iloc[-1]["entered_at"], zone)

        # today's lost minutes of this machine, biggest first
        lost = []
        if not downtime.empty:
            mine = downtime[downtime["machine"] == m].sort_values("minutes", ascending=False)
            lost = [{"reason": r["reason"], "minutes": number(r["minutes"])} for _, r in mine.head(4).iterrows()]

        out["machines"].append({
            "machine": m,
            "state": state,
            "can_pick": state in reasons.STOP_STATES,
            "reason": reason,
            "set_by": set_by,
            "set_at": set_at,
            "lost": lost,
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

    # the newest stop of this machine. A tap a few seconds late still lands on it.
    marks = ", ".join("?" for _ in reasons.STOP_STATES)
    stop = read(
        RESULTS_DB,
        f"SELECT ended_at FROM events WHERE machine = ? AND state IN ({marks}) "
        "ORDER BY started_at DESC LIMIT 1",
        (machine, *reasons.STOP_STATES),
    )
    if stop.empty:
        return jsonify({"error": f"no stop found for {machine} yet"}), 400

    # stamped inside that stop, so the calculation gives the reason to it
    reasons.add_reason(machine, stop.iloc[0]["ended_at"], reason, LIVE_DB)
    return jsonify({"ok": True})


@app.route("/")
def home():
    return PAGE


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#e2e3dc">
<title>Machine status</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {
    --bone: #e6e6e2;
    --card: #ededea;
    --ink: #121211;
    --muted: #6a6a64;
    --line: #d5d5ce;
    --panel: #0d0d0c;
    --panel-2: #1a1a18;
    --panel-line: #2b2b27;
    --panel-text: #f2f2ee;
    --panel-muted: #8f8f88;
    --orange: #ff3b00;
    --green: #5fc27e;
    --amber: #e3ab2e;
    --down-bg: #2a100a;
    --down-line: #7d3018;
    --serif: "Instrument Serif", "Iowan Old Style", Georgia, serif;
    --sans: "Inter", -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bone); color: var(--ink); font-family: var(--sans);
    font-feature-settings: "tnum" 1; -webkit-font-smoothing: antialiased;
    background-image:
      linear-gradient(to right, rgba(0,0,0,.028) 1px, transparent 1px),
      linear-gradient(to bottom, rgba(0,0,0,.028) 1px, transparent 1px);
    background-size: 120px 120px;
  }
  header {
    position: sticky; top: 0; z-index: 5; background: rgba(230,230,226,.92);
    backdrop-filter: blur(8px); border-bottom: 1px solid var(--line);
    padding: 16px 32px; display: flex; align-items: center; justify-content: space-between; gap: 16px;
  }
  .brand { font-family: var(--serif); font-size: 26px; line-height: 1; }
  .brand sup { color: var(--orange); font-size: 13px; font-family: var(--sans); }
  .live { font-size: 13px; color: var(--muted); display: flex; align-items: center; gap: 8px; }
  .live i { width: 7px; height: 7px; border-radius: 50%; background: var(--green); font-style: normal; }

  main { max-width: 1200px; margin: 0 auto; padding: 36px 32px 80px; }

  .kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }
  .kpi {
    background: linear-gradient(180deg, #f1f1ee 0%, var(--card) 100%);
    border: 1px solid rgba(255,255,255,.7); border-radius: 20px; padding: 22px 24px;
    box-shadow: 0 1px 2px rgba(0,0,0,.05), 0 8px 24px -14px rgba(0,0,0,.25);
  }
  .kpi b { display: block; font-family: var(--serif); font-size: 46px; font-weight: 400;
           line-height: 1; letter-spacing: -.01em; margin-bottom: 8px; }
  .kpi span { font-size: 13px; color: var(--muted); }

  h2 { font-family: var(--serif); font-weight: 400; font-size: 34px; margin: 48px 0 16px; letter-spacing: -.01em; }
  .sub { font-size: 14px; color: var(--muted); margin: -10px 0 18px; }

  .board { background: var(--panel); border-radius: 22px; padding: 20px; color: var(--panel-text);
           box-shadow: 0 30px 60px -30px rgba(0,0,0,.5); }
  .board-top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
  .board-top .left { display: flex; align-items: center; gap: 9px; font-size: 14px; }
  .board-top .left i { width: 7px; height: 7px; border-radius: 50%; background: var(--green); font-style: normal; }
  .tag { background: var(--panel-2); color: var(--panel-muted); font-size: 12px; font-weight: 600;
         border-radius: 999px; padding: 6px 12px; }

  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 12px; }
  .m { background: var(--panel-2); border: 1px solid transparent; border-radius: 14px; padding: 16px; }
  .m-top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
  .m-top b { font-size: 17px; font-weight: 600; }
  .st { font-size: 13px; color: var(--panel-muted); display: inline-flex; align-items: center; gap: 7px; }
  .st i { width: 7px; height: 7px; border-radius: 50%; background: currentColor; font-style: normal; }
  .m-line { font-size: 14px; margin-bottom: 4px; }
  .m-sub { font-size: 13px; color: var(--panel-muted); margin-bottom: 14px; }
  .bar { height: 4px; border-radius: 999px; background: #2e2e2a; overflow: hidden; margin-bottom: 8px; }
  .bar span { display: block; height: 100%; border-radius: 999px; background: #d8d8d2; }
  .m-foot { font-size: 12px; color: var(--panel-muted); }
  .m.RUNNING .st { color: var(--green); }
  .m.IDLE .st, .m.SETUP .st { color: var(--amber); }
  .m.ALARM { background: var(--down-bg); border-color: var(--down-line); }
  .m.ALARM .st { color: var(--orange); }
  .m.ALARM .bar span { background: var(--orange); }
  .m.SILENT, .m.OFF, .m.NO_DATA { opacity: .72; }

  .ask { background: var(--panel-2); border-radius: 14px; padding: 16px; margin-top: 12px;
         display: flex; gap: 18px; align-items: flex-start; flex-wrap: wrap; }
  .ask .q { font-size: 14px; flex: 1; min-width: 260px; }
  .ask .q b { font-weight: 600; }
  .ask .q .saved { color: var(--green); }
  .ask .q .warn { color: var(--orange); }
  .ask .pills { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }
  button {
    font: inherit; font-size: 13px; font-weight: 600; cursor: pointer; white-space: nowrap;
    border: 1px solid #343430; background: #232320; color: var(--panel-text);
    border-radius: 999px; padding: 9px 16px;
  }
  button:hover { border-color: #55554f; }
  button.on { background: var(--orange); border-color: var(--orange); color: #fff;
              box-shadow: 0 0 0 4px rgba(255,59,0,.22), 0 6px 18px -6px rgba(255,59,0,.7); }
  .lost { font-size: 12px; color: var(--panel-muted); margin-top: 10px; width: 100%; }
  .lost em { font-style: normal; color: var(--panel-text); }

  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; padding: 0 16px 10px 0; font-size: 12px; letter-spacing: .1em;
       text-transform: uppercase; color: var(--muted); font-weight: 600; border-bottom: 1px solid var(--line); }
  td { padding: 13px 16px 13px 0; font-size: 14px; border-bottom: 1px solid var(--line); vertical-align: top; }
  td.t { width: 76px; color: var(--muted); }
  td.m2 { width: 84px; font-weight: 600; }
  .none { font-size: 14px; color: var(--muted); padding: 14px 0; }

  @media (max-width: 820px) {
    header, main { padding-left: 18px; padding-right: 18px; }
    .kpis { grid-template-columns: repeat(2, 1fr); }
    .kpi b { font-size: 36px; }
    h2 { font-size: 28px; }
    .ask .pills { justify-content: flex-start; }
  }
</style>
</head>
<body>
<header>
  <div class="brand" id="client">Machine status<sup>+</sup></div>
  <div class="live"><i></i><span id="time">waiting for data</span></div>
</header>
<main>
  <div class="kpis" id="shop"></div>

  <h2>Live board</h2>
  <div class="sub">What is running, what is stopped, and why.</div>
  <div class="board">
    <div class="board-top">
      <div class="left"><i></i><span id="boardtime">Live board</span></div>
      <div class="tag" id="count"></div>
    </div>
    <div class="grid" id="cards"></div>
    <div id="stops"></div>
  </div>

  <h2>Open alerts</h2>
  <div id="open"></div>

  <h2>Cleared</h2>
  <div id="recent"></div>
</main>
<script>
var flash = {};

function show(v, unit) { return v === null || v === undefined ? "-" : v + (unit || ""); }
function nice(c) { return c.replace(/_/g, " "); }
function title(s) { return s.charAt(0) + s.slice(1).toLowerCase().replace(/_/g, " "); }

function kpi(value, name) {
  return '<div class="kpi"><b>' + value + "</b><span>" + name + "</span></div>";
}

function question(m) {
  var f = flash[m.machine];
  if (f && f.until > Date.now() && f.error) {
    return '<span class="warn">Not saved: ' + f.error + "</span>";
  }
  if (f && f.until > Date.now()) {
    return '<span class="saved">Saved. This stop counts as <b>' + nice(f.reason) + "</b>.</span>";
  }
  if (!m.reason) {
    return "<b>" + m.machine + "</b> stopped " + show(m.since_min, " min") +
           '. <span class="warn">No reason yet</span>, so it counts as unknown.';
  }
  if (m.set_by === "operator") {
    return "<b>" + m.machine + "</b> stopped " + show(m.since_min, " min") + ". Reason <b>" +
           nice(m.reason) + "</b>, set by operator at " + m.set_at + ".";
  }
  return "<b>" + m.machine + "</b> stopped " + show(m.since_min, " min") + ". Reason <b>" +
         nice(m.reason) + "</b>, guessed from the machine. Tap to correct it.";
}

function lostLine(m) {
  if (!m.lost || !m.lost.length) return "";
  var bits = m.lost.map(function (l) { return "<em>" + nice(l.reason) + "</em> " + l.minutes + " min"; });
  return '<div class="lost">Lost today on ' + m.machine + ": " + bits.join("&nbsp; &middot;&nbsp; ") + "</div>";
}

function stops(d) {
  var h = "";
  d.machines.forEach(function (m) {
    if (!m.can_pick) return;
    h += '<div class="ask"><div class="q">' + question(m) + '</div><div class="pills">';
    d.choices.forEach(function (c) {
      h += '<button data-machine="' + m.machine + '" data-reason="' + c + '"' +
           (c === m.reason ? ' class="on"' : "") + ">" + nice(c) + "</button>";
    });
    h += "</div>" + lostLine(m) + "</div>";
  });
  return h;
}

function table(rows, cleared) {
  if (!rows.length) return '<div class="none">Nothing here.</div>';
  var h = "<table><tr><th>Opened</th>" + (cleared ? "<th>Cleared</th>" : "") +
          "<th>Machine</th><th>Alert</th></tr>";
  rows.forEach(function (r) {
    h += '<tr><td class="t">' + r.opened + "</td>" +
         (cleared ? '<td class="t">' + r.cleared + "</td>" : "") +
         '<td class="m2">' + r.machine + "</td><td>" + r.message + "</td></tr>";
  });
  return h + "</table>";
}

function draw(d) {
  document.getElementById("client").innerHTML = d.client + "<sup>+</sup>";
  document.getElementById("time").textContent = d.time ? "Live " + d.time : "waiting for data";
  document.getElementById("boardtime").textContent = d.time ? "Live board  &middot;  " + d.time : "Live board";
  document.getElementById("boardtime").innerHTML = d.time ? "Live board &middot; " + d.time : "Live board";
  document.getElementById("count").textContent = d.machines.length + " machines";

  var s = d.shop;
  document.getElementById("shop").innerHTML = s ?
    kpi(show(s.oee, "%"), "Shop OEE") + kpi(show(s.availability, "%"), "Availability") +
    kpi(show(s.performance, "%"), "Performance") + kpi(s.parts, "Parts today") : "";

  var c = "";
  d.machines.forEach(function (m) {
    var sub = m.state === "SILENT" ? "No readings since " + m.last_reading :
              show(m.since_min, " min") + (m.reason ? " &middot; " + nice(m.reason) : "");
    var width = m.oee === null || m.oee === undefined ? 0 : Math.min(100, m.oee);
    c += '<div class="m ' + m.state + '"><div class="m-top"><b>' + m.machine + "</b>" +
         '<span class="st"><i></i>' + title(m.state) + "</span></div>" +
         '<div class="m-line">' + (m.program ? "Program " + m.program + " &middot; " : "") +
         m.parts + " parts</div>" +
         '<div class="m-sub">' + sub + "</div>" +
         '<div class="bar"><span style="width:' + width + '%"></span></div>' +
         '<div class="m-foot">OEE ' + show(m.oee, "%") + " &middot; Avail " + show(m.availability, "%") +
         " &middot; Perf " + show(m.performance, "%") + "</div></div>";
  });
  document.getElementById("cards").innerHTML = c || '<div class="m"><div class="m-sub">No data yet.</div></div>';

  document.getElementById("stops").innerHTML = stops(d);
  document.getElementById("open").innerHTML = table(d.open, false);
  document.getElementById("recent").innerHTML = table(d.recent, true);
}

document.addEventListener("click", function (ev) {
  var b = ev.target.closest("button[data-reason]");
  if (!b) return;
  fetch("/api/reason", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({machine: b.dataset.machine, reason: b.dataset.reason})
  }).then(function (r) { return r.json(); }).then(function (answer) {
    flash[b.dataset.machine] = {reason: b.dataset.reason, error: answer.error, until: Date.now() + 6000};
    load();
  }).catch(function () {
    flash[b.dataset.machine] = {error: "the server did not answer", until: Date.now() + 6000};
    load();
  });
});

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