"""
Unit tests for refined Google Drive syncing logic, notes metadata,
and roll GPS filtering.
"""

import unittest
import os
import tempfile
import json
import shutil
from unittest.mock import MagicMock, patch

from core.notes_manager import NotesManager
from core.drive_sync import DriveSyncHelper
import server


class TestDriveSyncLogic(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.notes_dir = os.path.join(self.test_dir, "notes")
        self.raw_dir = os.path.join(self.test_dir, "raw")
        os.makedirs(self.notes_dir, exist_ok=True)
        os.makedirs(self.raw_dir, exist_ok=True)
        self.notes_mgr = NotesManager(self.notes_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_notes_manager_gps_files_and_fallback(self):
        roll_id = "roll_2026-09-12T12-41-18_00-00"
        gps_files = ["G9C84118.FIT"]

        # 1. Save notes with gps_files
        saved = self.notes_mgr.save_notes(
            roll_id=roll_id,
            driver_name="Mia",
            buggy_name="Solaris",
            general_notes="Felt good around chute",
            segment_notes={"chute_turn": "clean line"},
            gps_files=gps_files
        )

        self.assertEqual(saved["gps_files"], ["G9C84118.FIT"])
        self.assertEqual(saved["driver_name"], "Mia")

        # Verify JSON file written with gps_files
        json_path, txt_path = self.notes_mgr._get_paths(roll_id)
        self.assertTrue(os.path.exists(json_path))
        self.assertTrue(os.path.exists(txt_path))

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.assertEqual(data.get("gps_files"), ["G9C84118.FIT"])

        with open(txt_path, "r", encoding="utf-8") as f:
            txt_content = f.read()
            self.assertIn("G9C84118.FIT", txt_content)
            self.assertIn("Mia", txt_content)

        # 2. Verify fallback to legacy {roll_id}.json
        legacy_id = "roll_legacy_test"
        clean_legacy = legacy_id.replace(":", "-").replace("/", "_")
        legacy_path = os.path.join(self.notes_dir, f"{clean_legacy}.json")
        with open(legacy_path, "w", encoding="utf-8") as f:
            json.dump({
                "roll_id": legacy_id,
                "driver_name": "Legacy Driver",
                "general_notes": "Old synced note without _notes suffix"
            }, f)

        loaded_legacy = self.notes_mgr.get_notes(legacy_id)
        self.assertEqual(loaded_legacy["driver_name"], "Legacy Driver")
        self.assertEqual(loaded_legacy["general_notes"], "Old synced note without _notes suffix")

    def test_roll_has_notes_predicate(self):
        # Empty notes
        self.assertFalse(server.roll_has_notes({}))
        self.assertFalse(server.roll_has_notes({"general_notes": "", "segment_notes": {}}))
        self.assertFalse(server.roll_has_notes({"general_notes": "   ", "segment_notes": {"chute": "  "}}))

        # Non-empty general notes
        self.assertTrue(server.roll_has_notes({"general_notes": "Solid freeroll"}))

        # Non-empty segment notes
        self.assertTrue(server.roll_has_notes({"general_notes": "", "segment_notes": {"chute": "Fast turn"}}))

    def test_get_syncable_files_filters_unnoted_rolls(self):
        syncable = server.get_syncable_files()

        # Any file in syncable must exist
        for f in syncable:
            self.assertTrue(os.path.exists(f), f"File {f} in syncable must exist")

        # Let's inspect the files in syncable:
        # GPS files in syncable must belong to rolls with notes
        rolls = server.get_all_grouped_rolls()
        rolls_with_notes = {r["roll_id"] for r in rolls if r["has_notes"]}
        rolls_without_notes = {r["roll_id"] for r in rolls if not r["has_notes"]}

        unnoted_gps_files = []
        for r in rolls:
            if not r["has_notes"]:
                for d in r.get("fused_roll", {}).get("watch_devices", []):
                    src = d.get("source_file")
                    if src and os.path.exists(src):
                        unnoted_gps_files.append(os.path.normpath(src))

        # Ensure NO unnoted GPS files appear in syncable_files
        for unnoted in unnoted_gps_files:
            # Only if it's not also shared with a noted roll
            if unnoted in syncable:
                # Check if it was legitimately shared
                shared = False
                for r in rolls:
                    if r["has_notes"]:
                        for d in r.get("fused_roll", {}).get("watch_devices", []):
                            if os.path.normpath(d.get("source_file", "")) == unnoted:
                                shared = True
                self.assertTrue(shared, f"Unnoted file {unnoted} should not be in syncable files!")

    @patch("core.drive_sync.build")
    def test_drive_sync_list_folder_files_pagination(self, mock_build):
        # Mock Drive API
        mock_service = MagicMock()
        mock_build.return_value = mock_service

        mock_files_resource = MagicMock()
        mock_service.files.return_value = mock_files_resource

        # Two pages of results
        page1 = {
            "nextPageToken": "token_page2",
            "files": [{"id": f"id_{i}", "name": f"file_{i}.fit", "mimeType": "application/octet-stream"} for i in range(100)]
        }
        page2 = {
            "files": [{"id": "id_101", "name": "file_101.fit", "mimeType": "application/octet-stream"}]
        }
        mock_files_resource.list.return_value.execute.side_effect = [page1, page2]

        config_path = os.path.join(self.test_dir, "mock_config.json")
        with open(config_path, "w") as f:
            json.dump({"google_drive": {"folder_id": "test_folder_id"}}, f)

        helper = DriveSyncHelper(config_path)
        with patch.object(helper, "get_credentials", return_value=MagicMock()):
            all_files = helper.list_folder_files()
            self.assertEqual(len(all_files), 101)
            self.assertEqual(all_files[100]["name"], "file_101.fit")

    @patch("core.drive_sync.build")
    def test_download_missing_files_routing_and_skipping(self, mock_build):
        mock_service = MagicMock()
        mock_build.return_value = mock_service

        config_path = os.path.join(self.test_dir, "mock_config.json")
        with open(config_path, "w") as f:
            json.dump({"google_drive": {"folder_id": "test_folder_id"}}, f)

        helper = DriveSyncHelper(config_path)

        # Create an existing local file
        existing_fit = os.path.join(self.raw_dir, "existing.fit")
        with open(existing_fit, "w") as f:
            f.write("local fit")

        # Remote has 1 existing FIT, 1 new FIT, 1 new JSON notes
        remote_files = [
            {"id": "r1", "name": "existing.fit", "mimeType": "application/octet-stream"},
            {"id": "r2", "name": "new_roll.fit", "mimeType": "application/octet-stream"},
            {"id": "r3", "name": "roll_test_notes.json", "mimeType": "application/json"},
        ]

        with patch.object(helper, "get_credentials", return_value=MagicMock()), \
             patch.object(helper, "list_folder_files", return_value=remote_files), \
             patch("core.drive_sync.MediaIoBaseDownload") as mock_downloader:

            # Make downloader simulate successful chunk download
            mock_dl_instance = MagicMock()
            mock_dl_instance.next_chunk.return_value = (None, True)
            mock_downloader.return_value = mock_dl_instance

            res = helper.download_missing_files({
                self.raw_dir: [".fit", ".gpx"],
                self.notes_dir: [".json", ".txt"]
            })

            self.assertTrue(res["success"])
            # existing.fit was skipped, so 2 files should be downloaded
            self.assertEqual(res["downloaded_count"], 2)
            downloaded_names = [d["name"] for d in res["downloaded_files"]]
            self.assertIn("new_roll.fit", downloaded_names)
            self.assertIn("roll_test_notes.json", downloaded_names)
    @patch("core.drive_sync.build")
    def test_sync_files_skips_existing_gps_and_overwrites_notes(self, mock_build):
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_files = MagicMock()
        mock_service.files.return_value = mock_files

        config_path = os.path.join(self.test_dir, "mock_config.json")
        with open(config_path, "w") as f:
            json.dump({"google_drive": {"folder_id": "test_folder_id"}}, f)

        helper = DriveSyncHelper(config_path)

        # Local files to sync
        local_fit = os.path.join(self.raw_dir, "G9C84118.FIT")
        with open(local_fit, "w") as f:
            f.write("fit data")

        local_notes = os.path.join(self.notes_dir, "roll_1_notes.json")
        with open(local_notes, "w") as f:
            f.write('{"general_notes": "updated notes"}')

        # Remote folder currently has:
        # - G9C84118.FIT (already exists!)
        # - roll_1_notes.json (already exists, should be updated!)
        # - roll_1_notes.json (accidental duplicate in Drive, should be cleaned up!)
        remote_files = [
            {"id": "gps_id_1", "name": "G9C84118.FIT", "mimeType": "application/octet-stream"},
            {"id": "note_id_1", "name": "roll_1_notes.json", "mimeType": "application/json"},
            {"id": "note_id_dup", "name": "roll_1_notes.json", "mimeType": "application/json"},
        ]

        with patch.object(helper, "get_credentials", return_value=MagicMock()), \
             patch.object(helper, "list_folder_files", return_value=remote_files):

            res = helper.sync_files([local_fit, local_notes])

            self.assertTrue(res["success"])
            self.assertEqual(res["skipped_count"], 1)   # G9C84118.FIT was skipped
            self.assertEqual(res["updated_count"], 1)   # roll_1_notes.json was updated
            self.assertEqual(res["uploaded_count"], 0)  # Neither was newly uploaded

            # Verify files().update was called for the note file
            mock_files.update.assert_called_once()
            call_kwargs = mock_files.update.call_args[1]
            self.assertEqual(call_kwargs["fileId"], "note_id_1")

            # Verify duplicate note file was deleted
            mock_files.delete.assert_called_with(fileId="note_id_dup", supportsAllDrives=True)


if __name__ == "__main__":
    unittest.main()
