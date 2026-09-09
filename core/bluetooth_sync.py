"""
Bluetooth and USB Auto-Sync Manager for Garmin Watches.
Prioritizes Bluetooth synchronization (monitoring Bluetooth exchange folders,
Garmin sync inboxes, and incoming BLE drops) while providing fallback USB auto-detection.
"""

import os
import glob
import shutil
import time
import threading
from typing import List, Dict, Any, Callable, Optional


class BluetoothSyncWatcher:
    """Monitors Bluetooth and USB paths for newly uploaded Garmin activity files."""

    def __init__(
        self,
        raw_dest_dir: str,
        bluetooth_inbox: str,
        on_new_file_callback: Optional[Callable[[str, str], None]] = None
    ):
        self.raw_dest_dir = os.path.abspath(raw_dest_dir)
        self.bluetooth_inbox = os.path.abspath(bluetooth_inbox)
        self.on_new_file_callback = on_new_file_callback

        os.makedirs(self.raw_dest_dir, exist_ok=True)
        os.makedirs(self.bluetooth_inbox, exist_ok=True)

        self.processed_files = set()
        self.running = False
        self.thread: Optional[threading.Thread] = None

        # Candidate paths to monitor for Bluetooth exchange on Windows
        user_home = os.path.expanduser("~")
        self.bluetooth_search_dirs = [
            self.bluetooth_inbox,
            os.path.join(user_home, "Documents", "Bluetooth Exchange Folder"),
            os.path.join(user_home, "Downloads"),
            os.path.join(user_home, "AppData", "Roaming", "Garmin", "Devices")
        ]

        # Scan existing files in raw_dest to prevent duplicate processing
        for f in glob.glob(os.path.join(self.raw_dest_dir, "*.*")):
            self.processed_files.add(os.path.basename(f))

    def start(self):
        """Start the background monitoring thread."""
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._watch_loop, daemon=True)
            self.thread.start()

    def stop(self):
        """Stop monitoring."""
        self.running = False

    def _watch_loop(self):
        while self.running:
            try:
                # 1. Check Bluetooth sync folders (Priority 1)
                self._check_bluetooth_paths()

                # 2. Check mounted USB drives for Garmin mass storage (Priority 2)
                self._check_usb_drives()
            except Exception as e:
                pass
            time.sleep(2.0)

    def _check_bluetooth_paths(self):
        for search_dir in self.bluetooth_search_dirs:
            if not os.path.exists(search_dir):
                continue
            for ext in ("*.fit", "*.gpx"):
                for filepath in glob.glob(os.path.join(search_dir, ext)):
                    fname = os.path.basename(filepath)
                    if fname not in self.processed_files:
                        self._ingest_file(filepath, connection_type="bluetooth")

    def _check_usb_drives(self):
        """Scan available Windows drive letters for GARMIN/ACTIVITY."""
        import string
        import ctypes

        # Check drive letters A-Z
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for letter in string.ascii_uppercase:
            if bitmask & 1:
                drive_path = f"{letter}:\\"
                activity_dir = os.path.join(drive_path, "GARMIN", "ACTIVITY")
                if os.path.exists(activity_dir):
                    for ext in ("*.fit",):
                        for filepath in glob.glob(os.path.join(activity_dir, ext)):
                            fname = os.path.basename(filepath)
                            if fname not in self.processed_files:
                                self._ingest_file(filepath, connection_type="usb")
            bitmask >>= 1

    def _ingest_file(self, filepath: str, connection_type: str):
        fname = os.path.basename(filepath)
        dest_path = os.path.join(self.raw_dest_dir, fname)

        # Handle filename collisions
        if os.path.exists(dest_path):
            base, ext = os.path.splitext(fname)
            dest_path = os.path.join(self.raw_dest_dir, f"{base}_{int(time.time())}{ext}")

        try:
            shutil.copy2(filepath, dest_path)
            self.processed_files.add(fname)
            if self.on_new_file_callback:
                self.on_new_file_callback(dest_path, connection_type)
        except Exception:
            pass

    def get_status(self) -> Dict[str, Any]:
        """Return status for the UI."""
        return {
            "bluetooth_active": True,
            "monitored_bluetooth_paths": [d for d in self.bluetooth_search_dirs if os.path.exists(d)],
            "raw_files_count": len(self.processed_files),
            "status": "Listening for Bluetooth sync & USB connections"
        }
