# GPS Save Our Drivers (Apex CMU Buggy Telemetry)

A lightweight, feedback-driven driver improvement and telemetry system for Carnegie Mellon Buggy freeroll analysis using Garmin Forerunner 35 watches.

---

## Why This System?

Raceday footage demonstrates that driver skill, steering commitment, and line exploration play a decisive role in freeroll speed. Rather than imposing rigid, robotic lines or struggling with fragile RTK GPS setups, **GPS Save Our Drivers** empowers drivers to:
1. **Explore their own decision space** (different turn-in points, scrubbing vs. holding roll speed, apex positioning).
2. **Isolate small, meaningful course segments** (rather than noisy full-roll averages) to assess exact entry, apex, and exit speeds.
3. **Receive rapid feedback at the staging** seconds after stepping out of the buggy.

---

## Hardware & Multi-Watch Setup

Apex has 3x Garmin Forerunner 35 watches recording at 1 Hz with Doppler speed.

### Multi-Watch Pushbar Mounting
- You can mount **1, 2, or all 3 Garmin watches** onto the buggy pushbar.
- **Auto-Detection of Same Roll**: Because Garmin internal clocks synchronize to GPS satellite atomic time upon satellite lock, the software automatically detects when multiple watches were recording the same roll.
- **Sensor Fusion & Spline Smoothing**: The system combines multiple 1 Hz streams, performing spatial averaging, Doppler velocity fusion, and local metric spline smoothing to eliminate individual 3–5m GPS noise and deliver a smooth, crisp speed and trajectory profile.

---

## Bluetooth Sync Priority

1. **Bluetooth Priority**: When watches sync via Bluetooth to the computer (via Garmin Express, Bluetooth File Exchange, or paired sync folders), the background watcher instantly ingests incoming files.
2. **USB Fallback**: If plugged in via USB, the system scans for `GARMIN/ACTIVITY` and copies files automatically.
3. **Drag-and-Drop**: You can also drag `.fit` or `.gpx` files directly into the web UI at any time.

---

## Schenley Park Freeroll Segments

The freeroll is divided into 6 isolated zones with standardized start and end gates:
1. **Hill 2 Crest & Drop** (Pusher release to initial gravity acceleration)
2. **Monument Curve** (Left/right transition past Westinghouse monument)
3. **Freeroll Straight** (High-speed straightaway leading down Schenley Drive)
4. **Chute Entry & Turn-in** (Critical braking, turn-in commitment, and line setup)
5. **Chute Apex & Sweep** (Peak lateral load, curbing proximity, and minimum speed)
6. **Chute Exit & Hill 3 Rollout** (Speed carry onto Frew Street toward Hill 3)

For each segment, the console computes:
- **$v_{in}$ (Entry Speed)**
- **$v_{min}$ / Apex Speed**
- **$v_{out}$ (Exit Speed)**
- **$\Delta v$ (Speed Held / Gained)**
- **$\Delta t$ (Segment Transit Time)**
- **Distance Traveled (Path Length Efficiency)**
- **Head-to-head comparison overlays** on a re-zeroed distance axis ($s \in [0, \text{segment length}]$).

---

## Quick Start

### 1. Requirements
Ensure Python 3.10+ is installed. Dependencies:
```bash
pip install -r requirements.txt
```

### 2. Launch
Double-click `run.bat` or run:
```bash
python run.py
```
This automatically launches the local server and opens `http://127.0.0.1:5000` in your default browser.

### 3. Running Automated Tests
```bash
python tests/test_pipeline.py
```

### 4. Request access to Google Drive
You will need `client_secret.json` and for someone with Google Cloud access to the official Apex Buggy account to give your Google account access.
You can still save your rolls data locally without Google Drive access. It is only needed to automatically upload to the Apex Google Drive folder.

---

## Driver Notes & Google Drive Sync
- Drivers can log impressions directly on the dashboard (e.g., *"Carried line high into Chute, felt much smoother, +2.5 mph at exit"*).
- Notes are saved locally in `data/notes/` as both JSON and readable `.txt` companion files.
- To link team Google Drive, set `folder_id` and service account credentials in `config/app_config.json`.
