"""
Interactive Google Drive OAuth Connection & Test Sync Script.
Run this script to authenticate your Google Account via browser and sync test files.
"""

import os
import sys
import glob

# Ensure project root is in sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from core.drive_sync import DriveSyncHelper

def main():
    config_path = os.path.join(BASE_DIR, "config", "app_config.json")
    helper = DriveSyncHelper(config_path)

    print("==================================================", flush=True)
    print("   APEX BUGGY - GOOGLE DRIVE OAUTH SYNC SETUP     ", flush=True)
    print("==================================================", flush=True)
    print(f"Target Folder ID: {helper.folder_id}", flush=True)
    print(f"Client Secret:    {helper.client_secret_path}", flush=True)
    print(f"Token Path:       {helper.token_path}\n", flush=True)

    status = helper.get_status()
    print(f"Current Status: {status['message']}", flush=True)

    if not status["authenticated"]:
        print("\n--> Starting OAuth Browser Flow...", flush=True)
        print("A browser window will open. Sign in with your Google account to grant access.", flush=True)
        auth_res = helper.authenticate()
        if not auth_res["success"]:
            print(f"\n[ERROR] Authentication failed: {auth_res.get('error')}", flush=True)
            return
        print("[SUCCESS] Authenticated successfully! token.json created.\n", flush=True)

    # Fetch folder details
    status = helper.get_status()
    if status.get("folder_name"):
        print(f"Connected to Google Drive Folder: '{status['folder_name']}'")

    # Collect files to sync
    raw_files = glob.glob(os.path.join(BASE_DIR, "data", "raw", "*.*"))
    notes_files = glob.glob(os.path.join(BASE_DIR, "data", "notes", "*.*"))
    all_files = raw_files + notes_files

    print(f"\nFound {len(all_files)} local test file(s) to sync:")
    for f in all_files:
        print(f"  - {os.path.basename(f)}")

    print("\n--> Uploading test data to Google Drive...")
    sync_res = helper.sync_files(all_files)
    if not sync_res["success"]:
        print(f"[ERROR] Sync failed: {sync_res.get('error')}")
        return

    print(f"\n[SUCCESS] {sync_res['message']}", flush=True)
    for item in sync_res.get("synced_files", []):
        print(f"   [OK] {item['name']} ({item['action']}) [ID: {item['id']}]", flush=True)

    # List folder contents to confirm
    print("\n--> Verifying files in Google Drive folder:", flush=True)
    folder_files = helper.list_folder_files()
    print(f"Found {len(folder_files)} file(s) in Drive folder:", flush=True)
    for ff in folder_files:
        size_kb = int(ff.get("size", 0)) / 1024
        print(f"   * {ff['name']} ({size_kb:.1f} KB) - ID: {ff['id']}", flush=True)

    print("\n==================================================", flush=True)
    print("   GOOGLE DRIVE SYNC VERIFIED SUCCESSFULLY!       ", flush=True)
    print("==================================================", flush=True)

if __name__ == "__main__":
    main()
