"""
Flask Web Server and API for GPS Save Our Drivers.
Provides live telemetry REST endpoints, roll management, multi-watch detection & fusion,
course segment isolation, driver notes persistence, and Bluetooth/USB auto-sync.
"""

import os
import glob
import json
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename

from core.garmin_parser import GarminParser
from core.multi_watch_fusion import MultiWatchFusion
from core.segmenter import CourseSegmenter
from core.notes_manager import NotesManager
from core.bluetooth_sync import BluetoothSyncWatcher
from core.drive_sync import DriveSyncHelper

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_RAW = os.path.join(BASE_DIR, "data", "raw")
DATA_PROCESSED = os.path.join(BASE_DIR, "data", "processed")
DATA_NOTES = os.path.join(BASE_DIR, "data", "notes")
DATA_BT = os.path.join(BASE_DIR, "data", "bluetooth_inbox")
CONFIG_FILE = os.path.join(BASE_DIR, "config", "app_config.json")

os.makedirs(DATA_RAW, exist_ok=True)
os.makedirs(DATA_PROCESSED, exist_ok=True)
os.makedirs(DATA_NOTES, exist_ok=True)
os.makedirs(DATA_BT, exist_ok=True)

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "web", "templates"),
    static_folder=os.path.join(BASE_DIR, "web", "static")
)

# Core singletons
segmenter = CourseSegmenter()
notes_mgr = NotesManager(DATA_NOTES)
drive_sync = DriveSyncHelper(CONFIG_FILE)

# In-memory parsed cache to keep app ultra-fast
parsed_cache = {}

def on_new_sync_file(filepath: str, conn_type: str):
    print(f"[{conn_type.upper()}] Ingested new activity: {filepath}")
    # Invalidate cache
    parsed_cache.clear()

bt_watcher = BluetoothSyncWatcher(
    raw_dest_dir=DATA_RAW,
    bluetooth_inbox=DATA_BT,
    on_new_file_callback=on_new_sync_file
)
bt_watcher.start()


def get_all_grouped_rolls():
    """Scan raw directory, parse activities, and group by same-roll multi-watch detection."""
    found_files = set()
    for ext in ("*.fit", "*.gpx"):
        for p in glob.glob(os.path.join(DATA_RAW, ext)):
            found_files.add(os.path.normpath(p))
    files = sorted(list(found_files))

    # Parse all files with cache
    parsed_runs = []
    for fpath in files:
        fname = os.path.basename(fpath)
        mtime = os.path.getmtime(fpath)
        cache_key = f"{fname}_{mtime}"

        if cache_key in parsed_cache:
            parsed_runs.append(parsed_cache[cache_key])
        else:
            try:
                run_data = GarminParser.parse_file(fpath)
                if run_data.get("point_count", 0) > 5:
                    parsed_cache[cache_key] = run_data
                    parsed_runs.append(run_data)
            except Exception as e:
                print(f"Error parsing {fname}: {e}")

    # Sort runs by start time
    parsed_runs.sort(key=lambda r: r.get("start_time", ""), reverse=True)

    # Group runs recorded during the same roll (multi-watch pairing)
    grouped = []
    used_indices = set()

    for i in range(len(parsed_runs)):
        if i in used_indices:
            continue
        current_group = [parsed_runs[i]]
        used_indices.add(i)

        for j in range(i + 1, len(parsed_runs)):
            if j in used_indices:
                continue
            if MultiWatchFusion.are_same_roll(parsed_runs[i], parsed_runs[j]):
                current_group.append(parsed_runs[j])
                used_indices.add(j)

        # Fuse group
        fused_roll = MultiWatchFusion.fuse_runs(current_group)
        roll_id = fused_roll["roll_id"]

        # Check for existing notes
        notes = notes_mgr.get_notes(roll_id)
        has_notes = bool(notes.get("general_notes") or notes.get("segment_notes"))

        display_time = "Unknown Time"
        if fused_roll.get("start_time"):
            try:
                dt = datetime.fromisoformat(fused_roll["start_time"])
                display_time = dt.strftime("%b %d, %I:%M:%S %p")
            except Exception:
                display_time = fused_roll["start_time"]

        watch_names = ", ".join(d["device_name"] for d in fused_roll["watch_devices"])

        grouped.append({
            "roll_id": roll_id,
            "display_name": f"{display_time} ({fused_roll['watch_count']} {'Watch' if fused_roll['watch_count'] == 1 else 'Watches Fused'})",
            "start_time": fused_roll.get("start_time"),
            "duration_sec": round(fused_roll.get("duration_sec", 0), 1),
            "total_dist_m": round(fused_roll.get("total_dist_m", 0), 1),
            "max_speed_mph": fused_roll.get("max_speed_mph", 0),
            "watch_count": fused_roll["watch_count"],
            "watch_summary": watch_names,
            "has_notes": has_notes,
            "fused_roll": fused_roll
        })

    return grouped


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    return jsonify({
        "bluetooth": bt_watcher.get_status(),
        "drive": drive_sync.get_status(),
        "total_rolls": len(get_all_grouped_rolls())
    })


@app.route("/api/rolls")
def api_rolls():
    rolls = get_all_grouped_rolls()
    summaries = [
        {
            "roll_id": r["roll_id"],
            "display_name": r["display_name"],
            "start_time": r["start_time"],
            "duration_sec": r["duration_sec"],
            "total_dist_m": r["total_dist_m"],
            "max_speed_mph": r["max_speed_mph"],
            "watch_count": r["watch_count"],
            "watch_summary": r["watch_summary"],
            "has_notes": r["has_notes"]
        }
        for r in rolls
    ]
    return jsonify({"rolls": summaries})


@app.route("/api/roll/<roll_id>")
def api_roll_detail(roll_id):
    rolls = get_all_grouped_rolls()
    match = next((r for r in rolls if r["roll_id"] == roll_id), None)
    if not match:
        return jsonify({"error": "Roll not found"}), 404

    fused_roll = match["fused_roll"]
    segmented = segmenter.segment_roll(fused_roll)
    notes = notes_mgr.get_notes(roll_id)

    return jsonify({
        "roll": fused_roll,
        "segments": segmented["segments"],
        "course_segments_meta": segmenter.segments,
        "notes": notes
    })


@app.route("/api/compare")
def api_compare():
    """Compare two rolls on a selected segment."""
    roll1_id = request.args.get("roll1")
    roll2_id = request.args.get("roll2")
    seg_id = request.args.get("segment", "full")

    rolls = get_all_grouped_rolls()
    r1 = next((r for r in rolls if r["roll_id"] == roll1_id), None)
    r2 = next((r for r in rolls if r["roll_id"] == roll2_id), None)

    if not r1 or not r2:
        return jsonify({"error": "One or both rolls not found"}), 400

    r1_segmented = segmenter.segment_roll(r1["fused_roll"])
    r2_segmented = segmenter.segment_roll(r2["fused_roll"])

    if seg_id == "full":
        # Full roll comparison
        r1_records = r1["fused_roll"]["records"]
        r2_records = r2["fused_roll"]["records"]
        metrics1 = {
            "entry_speed_mph": r1_records[0]["speed_mph"] if r1_records else 0,
            "min_speed_mph": min((r["speed_mph"] for r in r1_records), default=0),
            "apex_speed_mph": min((r["speed_mph"] for r in r1_records), default=0),
            "exit_speed_mph": r1_records[-1]["speed_mph"] if r1_records else 0,
            "max_speed_mph": r1["max_speed_mph"],
            "transit_time_sec": r1["duration_sec"],
            "distance_m": r1["total_dist_m"]
        }
        metrics2 = {
            "entry_speed_mph": r2_records[0]["speed_mph"] if r2_records else 0,
            "min_speed_mph": min((r["speed_mph"] for r in r2_records), default=0),
            "apex_speed_mph": min((r["speed_mph"] for r in r2_records), default=0),
            "exit_speed_mph": r2_records[-1]["speed_mph"] if r2_records else 0,
            "max_speed_mph": r2["max_speed_mph"],
            "transit_time_sec": r2["duration_sec"],
            "distance_m": r2["total_dist_m"]
        }
        rebased_r1 = r1_records
        rebased_r2 = r2_records
    else:
        seg1 = r1_segmented["segments"].get(seg_id)
        seg2 = r2_segmented["segments"].get(seg_id)
        if not seg1 or not seg2:
            return jsonify({"error": f"Segment {seg_id} not available in both rolls"}), 400

        metrics1 = seg1["metrics"]
        metrics2 = seg2["metrics"]
        rebased_r1 = seg1["records"]
        rebased_r2 = seg2["records"]

    # Calculate actionable deltas (Roll 2 vs Roll 1)
    deltas = {
        "delta_entry_speed_mph": round(metrics2["entry_speed_mph"] - metrics1["entry_speed_mph"], 2),
        "delta_min_speed_mph": round(metrics2["min_speed_mph"] - metrics1["min_speed_mph"], 2),
        "delta_apex_speed_mph": round(metrics2["apex_speed_mph"] - metrics1["apex_speed_mph"], 2),
        "delta_exit_speed_mph": round(metrics2["exit_speed_mph"] - metrics1["exit_speed_mph"], 2),
        "delta_transit_time_sec": round(metrics2["transit_time_sec"] - metrics1["transit_time_sec"], 2),
        "delta_distance_m": round(metrics2["distance_m"] - metrics1["distance_m"], 2)
    }

    return jsonify({
        "segment_id": seg_id,
        "roll1": {
            "roll_id": roll1_id,
            "display_name": r1["display_name"],
            "metrics": metrics1,
            "records": rebased_r1
        },
        "roll2": {
            "roll_id": roll2_id,
            "display_name": r2["display_name"],
            "metrics": metrics2,
            "records": rebased_r2
        },
        "deltas": deltas
    })


@app.route("/api/notes/<roll_id>", methods=["GET", "POST"])
def api_notes(roll_id):
    if request.method == "POST":
        data = request.json or {}
        saved = notes_mgr.save_notes(
            roll_id=roll_id,
            driver_name=data.get("driver_name", ""),
            buggy_name=data.get("buggy_name", ""),
            general_notes=data.get("general_notes", ""),
            segment_notes=data.get("segment_notes", {})
        )
        # If Google Drive is authenticated, auto-sync driver notes
        try:
            note_file = os.path.join(DATA_NOTES, f"{roll_id}_notes.json")
            note_txt = os.path.join(DATA_NOTES, f"{roll_id}_notes.txt")
            sync_targets = [f for f in (note_file, note_txt) if os.path.exists(f)]
            if sync_targets and drive_sync.get_status().get("authenticated"):
                drive_sync.sync_files(sync_targets)
        except Exception as e:
            print(f"Error auto-syncing notes to Drive: {e}")

        return jsonify({"success": True, "notes": saved})
    else:
        return jsonify(notes_mgr.get_notes(roll_id))


@app.route("/api/drive/status")
def api_drive_status():
    status = drive_sync.get_status()
    folder_files = []
    if status.get("authenticated"):
        folder_files = drive_sync.list_folder_files()
    return jsonify({
        "status": status,
        "folder_files": folder_files
    })


@app.route("/api/drive/auth", methods=["POST", "GET"])
def api_drive_auth():
    res = drive_sync.authenticate()
    return jsonify(res)


@app.route("/api/drive/sync", methods=["POST"])
def api_drive_sync():
    """Two-way sync: Upload local telemetry/notes and download routed remote files."""
    # 1. Upload local files
    raw_files = glob.glob(os.path.join(DATA_RAW, "*.*"))
    notes_files = glob.glob(os.path.join(DATA_NOTES, "*.*"))
    all_files = raw_files + notes_files

    upload_res = drive_sync.sync_files(all_files)

    # 2. Download missing remote files with automatic extension routing
    routing_config = {
        DATA_RAW: [".fit", ".gpx"],
        DATA_NOTES: [".json", ".txt", ".log"]
    }

    download_res = drive_sync.download_missing_files(routing_config) if upload_res.get("success") else {
        "success": False, 
        "error": "Skipped download due to upload failure."
    }

    # 3. Refresh directory listing
    folder_files = drive_sync.list_folder_files() if upload_res.get("success") else []

    overall_success = upload_res.get("success", False) and download_res.get("success", False)

    return jsonify({
        "success": overall_success,
        "upload_result": upload_res,
        "download_result": download_res,
        "folder_files": folder_files
    })


@app.route("/api/upload", methods=["POST"])
def api_upload():
    """Manual upload fallback for FIT/GPX files."""
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    filename = secure_filename(file.filename)
    dest_path = os.path.join(DATA_RAW, filename)
    file.save(dest_path)
    parsed_cache.clear()

    # If Drive is authenticated, also sync new file to Drive
    if drive_sync.get_status().get("authenticated"):
        try:
            drive_sync.sync_files([dest_path])
        except Exception as e:
            print(f"Error auto-syncing uploaded file: {e}")

    return jsonify({
        "success": True,
        "message": f"Successfully imported {filename}",
        "filename": filename
    })


@app.route("/api/scan", methods=["POST"])
def api_scan():
    """Trigger manual rescan of Bluetooth exchange & USB paths."""
    bt_watcher._check_bluetooth_paths()
    bt_watcher._check_usb_drives()
    parsed_cache.clear()
    return jsonify({"success": True, "status": bt_watcher.get_status()})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)

