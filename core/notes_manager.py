"""
Driver Notes Manager for GPS Save Our Drivers.
Saves and loads driver notes (stored as both structured JSON and plain text)
alongside GPS files so feedback is immediately captured and shareable.
"""

import json
import os
from datetime import datetime
from typing import Dict, Any, Optional, List


class NotesManager:
    """Manages reading and writing driver notes."""

    def __init__(self, notes_dir: Optional[str] = None):
        if notes_dir is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            notes_dir = os.path.join(base_dir, "data", "notes")
        self.notes_dir = notes_dir
        os.makedirs(self.notes_dir, exist_ok=True)
        self._notes_cache: Dict[str, Dict[str, Any]] = {}

    def _get_paths(self, roll_id: str):
        clean_id = roll_id.replace(":", "-").replace("/", "_")
        json_path = os.path.join(self.notes_dir, f"{clean_id}_notes.json")
        txt_path = os.path.join(self.notes_dir, f"{clean_id}_notes.txt")
        return json_path, txt_path

    def save_notes(
        self,
        roll_id: str,
        driver_name: str,
        buggy_name: str,
        general_notes: str,
        segment_notes: Optional[Dict[str, str]] = None,
        gps_files: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Save driver notes in both JSON and human-readable TXT format."""
        json_path, txt_path = self._get_paths(roll_id)
        now_str = datetime.now().isoformat()

        # If gps_files not provided, preserve any previously recorded gps_files
        if gps_files is None:
            existing = self.get_notes(roll_id)
            gps_files = existing.get("gps_files", [])

        note_data = {
            "roll_id": roll_id,
            "driver_name": driver_name or "Unknown Driver",
            "buggy_name": buggy_name or "Apex Buggy",
            "saved_at": now_str,
            "general_notes": general_notes or "",
            "segment_notes": segment_notes or {},
            "gps_files": [os.path.basename(f) for f in gps_files] if gps_files else []
        }

        # Save JSON
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(note_data, f, indent=2)

        # Save Plain Text companion file
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(f"=== APEX BUGGY DRIVER NOTES ===\n")
            f.write(f"Roll ID: {roll_id}\n")
            f.write(f"Date/Time: {now_str}\n")
            f.write(f"Driver: {note_data['driver_name']}\n")
            f.write(f"Buggy: {note_data['buggy_name']}\n")
            if note_data["gps_files"]:
                f.write(f"GPS File(s): {', '.join(note_data['gps_files'])}\n")
            f.write("\n")
            f.write(f"--- GENERAL NOTES ---\n")
            f.write(f"{general_notes.strip() if general_notes else '(No general notes)'}\n\n")

            if segment_notes:
                f.write(f"--- SEGMENT FEEDBACK ---\n")
                for seg_id, text in segment_notes.items():
                    if text and text.strip():
                        f.write(f"[{seg_id}]: {text.strip()}\n")

        self._notes_cache[roll_id] = note_data
        return note_data

    def get_notes(self, roll_id: str) -> Dict[str, Any]:
        """Load driver notes for a roll, or empty template if none exist (in-memory cached)."""
        if roll_id in self._notes_cache:
            return self._notes_cache[roll_id]

        clean_id = roll_id.replace(":", "-").replace("/", "_")
        json_path, _ = self._get_paths(roll_id)
        
        # Check standard {clean_id}_notes.json first
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                data.setdefault("gps_files", [])
                self._notes_cache[roll_id] = data
                return data

        # Fallback to legacy {clean_id}.json if present
        legacy_json = os.path.join(self.notes_dir, f"{clean_id}.json")
        if os.path.exists(legacy_json):
            try:
                with open(legacy_json, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    data.setdefault("gps_files", [])
                    self._notes_cache[roll_id] = data
                    return data
            except Exception:
                pass

        default_note = {
            "roll_id": roll_id,
            "driver_name": "",
            "buggy_name": "Apex Buggy",
            "saved_at": None,
            "general_notes": "",
            "segment_notes": {},
            "gps_files": []
        }
        self._notes_cache[roll_id] = default_note
        return default_note
