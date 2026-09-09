"""
Integration test for the Flask server and API endpoints.
"""

import urllib.request
import json
import time
import subprocess
import sys
import os

def test_server():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.Popen([sys.executable, "server.py"], cwd=base_dir)
    time.sleep(2.5)

    try:
        # 1. Test index page
        with urllib.request.urlopen("http://127.0.0.1:5000/") as r:
            html = r.read().decode("utf-8")
            assert "GPS SAVE OUR DRIVERS" in html, "Dashboard HTML missing title"
            print("[PASS] Index page rendered successfully (status 200)")

        # 2. Test status endpoint
        with urllib.request.urlopen("http://127.0.0.1:5000/api/status") as r:
            status_data = json.loads(r.read().decode("utf-8"))
            assert status_data["bluetooth"]["bluetooth_active"], "Bluetooth watcher should be active"
            print(f"[PASS] Status API OK (Bluetooth active, {status_data['total_rolls']} rolls recorded)")

        # 3. Test rolls endpoint
        with urllib.request.urlopen("http://127.0.0.1:5000/api/rolls") as r:
            rolls_data = json.loads(r.read().decode("utf-8"))
            rolls = rolls_data["rolls"]
            assert len(rolls) >= 2, "Expected at least 2 rolls"
            print(f"[PASS] Rolls API returned {len(rolls)} grouped rolls:")
            for r_item in rolls:
                print(f"       * {r_item['display_name']} | Max: {r_item['max_speed_mph']} mph | Watches: {r_item['watch_count']}")

        # 4. Test compare endpoint on The Chute
        r1_id = rolls[1]["roll_id"]
        r2_id = rolls[0]["roll_id"]
        url_comp = f"http://127.0.0.1:5000/api/compare?roll1={r1_id}&roll2={r2_id}&segment=chute_turn"
        with urllib.request.urlopen(url_comp) as r:
            comp_data = json.loads(r.read().decode("utf-8"))
            deltas = comp_data["deltas"]
            print(f"[PASS] Compare API OK for The Chute:")
            print(f"       Delta Entry Speed: {deltas['delta_entry_speed_mph']} mph")
            print(f"       Delta Min/Apex Speed: {deltas['delta_min_speed_mph']} mph")
            print(f"       Delta Exit Speed: {deltas['delta_exit_speed_mph']} mph")
            print(f"       Delta Transit Time: {deltas['delta_transit_time_sec']} s")

        # 5. Test notes endpoint
        notes_url = f"http://127.0.0.1:5000/api/notes/{r2_id}"
        req = urllib.request.Request(
            notes_url,
            data=json.dumps({"driver_name": "Sarah", "buggy_name": "Apex Firebird", "general_notes": "Great test roll"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req) as r:
            note_res = json.loads(r.read().decode("utf-8"))
            assert note_res["success"], "Notes save failed"
            print("[PASS] Notes API saved driver reflections successfully")

        print("\n=== ALL SERVER & API INTEGRATION TESTS PASSED ===")

    finally:
        proc.terminate()

if __name__ == "__main__":
    test_server()
