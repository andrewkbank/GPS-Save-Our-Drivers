"""
Driver Notes Manager for GPS Save Our Drivers.
Saves and loads driver notes (stored as both structured JSON and plain text)
alongside GPS files so feedback is immediately captured and shareable.
"""

import json
import os
from datetime import datetime
from typing import Dict, Any, Optional


class NotesManager:
    """Manages reading and writing driver notes."""

    def __init__(self, notes_dir: Optional[str] = None):
        if notes_dir is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            notes_dir = os.path.join(base_dir, "data", "notes")
        self.notes_dir = notes_dir
        os.makedirs(self.notes_dir, exist_ok=True)

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
        segment_notes: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """Save driver notes in both JSON and human-readable TXT format."""
        json_path, txt_path = self._get_paths(roll_id)
        now_str = datetime.now().isoformat()

        note_data = {
            "roll_id": roll_id,
            "driver_name": driver_name or "Unknown Driver",
            "buggy_name": buggy_name or "Apex Buggy",
            "saved_at": now_str,
            "general_notes": general_notes or "",
            "segment_notes": segment_notes or {}
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
            f.write(f"Buggy: {note_data['buggy_name']}\n\n")
            f.write(f"--- GENERAL NOTES ---\n")
            f.write(f"{general_notes.strip() if general_notes else '(No general notes)'}\n\n")

            if segment_notes:
                f.write(f"--- SEGMENT FEEDBACK ---\n")
                for seg_id, text in segment_notes.items():
                    if text and text.strip():
                        f.write(f"[{seg_id}]: {text.strip()}\n")

        return note_data

    def get_notes(self, roll_id: str) -> Dict[str, Any]:
        """Load driver notes for a roll, or empty template if none exist."""
        json_path, _ = self._get_paths(roll_id)
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {
            "roll_id": roll_id,
            "driver_name": "",
            "buggy_name": "Apex Buggy",
            "saved_at": None,
            "general_notes": "",
            "segment_notes": {}
        }
