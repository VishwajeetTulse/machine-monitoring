import os, ctypes

LIB_DIR = r"D:\machine-monitoring\lib"

os.add_dll_directory(LIB_DIR)
lib = ctypes.CDLL(os.path.join(LIB_DIR, "Fwlib32.dll"))

print("LOADED OK")
for name in ["cnc_allclibhndl3", "cnc_freelibhndl", "cnc_statinfo",
             "cnc_rdspmeter", "cnc_rdtimer", "cnc_acts",
             "cnc_rdprgnum", "cnc_alarm2"]:
    print(f"  {name:20s} {getattr(lib, name)}")