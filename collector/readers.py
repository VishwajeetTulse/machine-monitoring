# a reader gives ONE reading of ONE machine. The collector does not care which kind.
#   FakeReader  plays the simulator CSV, so the whole system can be tried without a machine
#   FocasReader asks a real FANUC controller. Read-only: it never sends anything to it.

import ctypes
from datetime import datetime, timezone

import pandas as pd

from readings import COLUMNS


class ReaderError(Exception):
    # the machine did not answer, or answered nonsense
    pass


def now_text():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


class FakeReader:

    def __init__(self, machine, csv_path, outage=None):
        self.code = machine["code"]
        df = pd.read_csv(csv_path, low_memory=False)
        df = df[df["machine_code"] == self.code]
        if df.empty:
            raise ReaderError(f"no rows for machine {self.code} in {csv_path}")

        # keep only the real columns. The simulator's own answers are dropped.
        df = df.sort_values("timestamp")[[c for c in COLUMNS if c in df.columns]]
        self.rows = df.astype(object).where(df.notna(), None).to_dict("records")
        self.next = 0
        self.outage = outage            # (start_second, length_seconds) after starting
        self.began = datetime.now().timestamp()

    def read(self):
        gone = datetime.now().timestamp() - self.began
        if self.outage and self.outage[0] <= gone < self.outage[0] + self.outage[1]:
            raise ReaderError("simulated outage")

        row = dict(self.rows[self.next % len(self.rows)])
        self.next += 1
        row["timestamp"] = now_text()
        return row

    def close(self):
        pass


# ---------------------------------------------------------------------------
# FOCAS reader.
# NOT TESTED ON A REAL CONTROLLER YET. The calls below follow the FANUC FOCAS
# library names, but the structure layouts differ between controller series.
# Before trusting it on a machine, check these against Fwlib32.h of that series:
#   - ODBPRO (program number): short or long fields
#   - parameter numbers for the counters (they are set per machine in the file)
#   - alarm number: now only "an alarm is on" (1), not the real alarm number
# ---------------------------------------------------------------------------

class ODBST(ctypes.Structure):
    _fields_ = [(n, ctypes.c_short) for n in
                ("hdck", "tmmode", "aut", "run", "motion", "mstb", "emergency", "alarm", "edit")]


class ODBACT(ctypes.Structure):
    _fields_ = [("dummy", ctypes.c_short * 2), ("data", ctypes.c_long)]


class ODBPRO(ctypes.Structure):
    _fields_ = [("dummy", ctypes.c_short * 2), ("data", ctypes.c_long), ("mdata", ctypes.c_long)]


class IODBPSD(ctypes.Structure):
    # one 4-byte parameter value
    _fields_ = [("datano", ctypes.c_short), ("type", ctypes.c_short), ("ldata", ctypes.c_long)]


# parameter numbers most FANUC controllers use for the part counters.
# Others (cycle time, cutting time...) differ by machine, so they come from the file.
DEFAULT_PARAMS = {"parts_today": 6711, "parts_life": 6712}


class FocasReader:

    def __init__(self, machine, dll="lib/Fwlib32.dll", timeout=3):
        self.code = machine["code"]
        self.machine = machine
        self.timeout = timeout
        self.params = {**DEFAULT_PARAMS, **machine.get("params", {})}
        self.ideal = {str(k): v for k, v in machine.get("ideal_cycle_s", {}).items()}

        if not hasattr(ctypes, "WinDLL"):
            raise ReaderError("FOCAS needs Windows and 32-bit Python (use venv32)")
        try:
            self.lib = ctypes.WinDLL(dll)
        except OSError as e:
            raise ReaderError(f"cannot load {dll}: {e}")
        self.handle = None

    def connect(self):
        h = ctypes.c_ushort(0)
        rc = self.lib.cnc_allclibhndl3(self.machine["ip"].encode(), int(self.machine.get("port", 8193)),
                                       self.timeout, ctypes.byref(h))
        if rc != 0:
            raise ReaderError(f"cannot connect to {self.machine['ip']} (code {rc})")
        self.handle = h

    def close(self):
        if self.handle is not None:
            self.lib.cnc_freelibhndl(self.handle)
            self.handle = None

    def check(self, rc, what):
        if rc != 0:
            # drop the connection, the next read connects again
            self.close()
            raise ReaderError(f"{what} failed (code {rc})")

    def parameter(self, number):
        p = IODBPSD()
        self.check(self.lib.cnc_rdparam(self.handle, number, 0, 8, ctypes.byref(p)), f"parameter {number}")
        return p.ldata

    def read(self):
        if self.handle is None:
            self.connect()

        st = ODBST()
        self.check(self.lib.cnc_statinfo(self.handle, ctypes.byref(st)), "cnc_statinfo")

        speed = ODBACT()
        self.check(self.lib.cnc_acts(self.handle, ctypes.byref(speed)), "cnc_acts")

        prog = ODBPRO()
        self.check(self.lib.cnc_rdprgnum(self.handle, ctypes.byref(prog)), "cnc_rdprgnum")
        number = int(prog.data)

        row = {
            "machine_code": self.code,
            "machine_id": self.machine.get("id", self.code),
            "timestamp": now_text(),
            # run: 3 = cycle start. Everything else counts as stopped.
            "execution": "ACTIVE" if st.run == 3 else "STOPPED",
            # aut: 1 = memory (automatic)
            "mode": "AUTOMATIC" if st.aut == 1 else "MANUAL",
            "program_number": number,
            "program_name": f"O{number:04d}",
            "spindle_speed": int(speed.data),
            # an alarm or an emergency stop. The real alarm number is not read yet.
            "alarm_code": 1 if (st.alarm or st.emergency) else 0,
        }

        # counters, times and anything else the file gives a parameter number for
        for column, number_ in self.params.items():
            if column in COLUMNS:
                row[column] = int(self.parameter(number_))

        # the ideal cycle is not on the controller. It comes from the machines file.
        ideal = self.ideal.get(str(number))
        if ideal is not None:
            row["ideal_cycle_ms"] = int(float(ideal) * 1000)

        return row