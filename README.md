# Machine Monitoring

CNC/VMC machine monitoring — FOCAS data collection, state detection,
downtime analysis and OEE.

## Layout

- `collector/` — FOCAS client, reads from FANUC controllers
- `operations/` — monitoring logic, one file per operation
- `analysis/` — exploration scripts
- `data/` — generated datasets (gitignored)
- `lib/` — FOCAS DLLs (gitignored, see Setup)

## Setup

Two environments are needed. The FOCAS DLLs are 32-bit, so the collector
runs on 32-bit Python; everything else runs on 64-bit.

    python -m venv venv32    # 32-bit Python, collector only
    python -m venv venv64    # 64-bit Python, analysis and operations

`lib/` is not in this repo. The FOCAS libraries are licensed by FANUC and
must be obtained from FANUC directly.

## Generating test data

    python cnc_simulator.py --hours 8 --machines 3 --out data/cnc_sim_data.csv