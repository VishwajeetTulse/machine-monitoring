# when the shop is meant to be working, from the shifts and breaks in the settings

import pandas as pd

from settings import break_minutes, shift_minutes
from test_classification import IDLE, SETUP, OFF, NO_DATA, NOT_RESPONDING, PLANNED_STOP

ONE_SECOND = pd.Timedelta(seconds=1)

# a running machine or an alarm is never cut at a break:
# cutting an alarm in two would count one failure as two
CAN_BE_CUT = {IDLE, SETUP, OFF, NO_DATA, NOT_RESPONDING}


def working_day_start(now, settings):
    # a working day starts with its first shift, so a night shift stays in one day
    local = now.tz_convert(settings["timezone"])
    first = min(shift_minutes(s)[0] for s in settings["shifts"])
    start = local.normalize() + pd.Timedelta(minutes=first)
    if local < start:
        start -= pd.Timedelta(days=1)
    return start.tz_convert("UTC")


def working_windows(settings, first, last):
    # (start, end) in UTC, end not included, covering first to last

    zone = settings["timezone"]
    windows = []

    days = pd.date_range(
        first.tz_convert(zone).normalize() - pd.Timedelta(days=1),
        last.tz_convert(zone).normalize(),
        freq="D",
    )

    for day in days:
        if day.day_name() in settings["weekly_off"]:
            continue

        for shift in settings["shifts"]:
            begin, finish = shift_minutes(shift)
            cursor = begin

            for break_start, break_end in break_minutes(shift):
                if break_start > cursor:
                    windows.append((day, cursor, break_start))
                cursor = max(cursor, break_end)
            if cursor < finish:
                windows.append((day, cursor, finish))

    spans = sorted(
        ((day + pd.Timedelta(minutes=a)).tz_convert("UTC"),
         (day + pd.Timedelta(minutes=b)).tz_convert("UTC"))
        for day, a, b in windows
    )

    # shifts that follow each other make one window
    merged = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def is_working(now, settings):
    return any(a <= now < b for a, b in working_windows(settings, now, now))


def pieces(start, last, windows):
    # cuts start..last (both included) into (from, to, inside a working window)
    out = []
    cursor = start

    for window_start, window_end in windows:
        window_last = window_end - ONE_SECOND
        if window_last < cursor:
            continue
        if window_start > last:
            break
        if window_start > cursor:
            out.append((cursor, window_start - ONE_SECOND, False))
            cursor = window_start
        stop = min(last, window_last)
        out.append((cursor, stop, True))
        cursor = stop + ONE_SECOND
        if cursor > last:
            return out

    if cursor <= last:
        out.append((cursor, last, False))
    return out


def apply_calendar(events, settings):
    # time outside the working windows becomes PLANNED_STOP.
    # A machine that reports OFF during working time has simply stopped working.

    if events.empty:
        return events

    windows = working_windows(settings, events["started_at"].min(), events["ended_at"].max())
    rows = []

    for e in events.to_dict("records"):
        if e["state"] not in CAN_BE_CUT:
            rows.append(e)
            continue

        for start, last, working in pieces(e["started_at"], e["ended_at"], windows):
            piece = dict(e, started_at=start, ended_at=last,
                         duration_s=int((last - start).total_seconds()) + 1)
            if not working:
                piece["state"] = PLANNED_STOP
            elif e["state"] == OFF:
                piece["state"] = NOT_RESPONDING
            if piece["state"] == NOT_RESPONDING:
                piece["reason"] = NOT_RESPONDING
            rows.append(piece)

    return pd.DataFrame(rows, columns=events.columns).reset_index(drop=True)
