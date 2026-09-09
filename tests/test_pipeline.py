"""
Unit and Integration Tests for GPS Save Our Drivers.
Validates:
1. GarminParser telemetry extraction
2. MultiWatchFusion same-roll detection and spline smoothing
3. CourseSegmenter segment isolation & metric extraction
4. NotesManager feedback persistence
"""

import os
import sys
import unittest

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.garmin_parser import GarminParser
from core.multi_watch_fusion import MultiWatchFusion
from core.segmenter import CourseSegmenter
from core.notes_manager import NotesManager


class TestBuggyTelemetryPipeline(unittest.TestCase):

    def setUp(self):
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.raw_dir = os.path.join(self.base_dir, "data", "raw")
        self.file_r1_w1 = os.path.join(self.raw_dir, "roll_1_watch1_garmin35.gpx")
        self.file_r1_w2 = os.path.join(self.raw_dir, "roll_1_watch2_garmin35.gpx")
        self.file_r2_w1 = os.path.join(self.raw_dir, "roll_2_watch1_garmin35.gpx")

    def test_01_parser(self):
        self.assertTrue(os.path.exists(self.file_r1_w1), "Sample file 1 must exist")
        res = GarminParser.parse_file(self.file_r1_w1)
        self.assertGreater(res["point_count"], 30)
        self.assertGreater(res["max_speed_mph"], 20.0)
        self.assertGreater(res["total_dist_m"], 600.0)
        first_pt = res["records"][0]
        self.assertIn("lat", first_pt)
        self.assertIn("speed_mph", first_pt)
        print(f"\n[Test 1 Passed] Parsed {res['point_count']} points, max speed: {res['max_speed_mph']} mph")

    def test_02_multi_watch_detection(self):
        run_1_w1 = GarminParser.parse_file(self.file_r1_w1)
        run_1_w2 = GarminParser.parse_file(self.file_r1_w2)
        run_2_w1 = GarminParser.parse_file(self.file_r2_w1)

        # Roll 1 Watch 1 & Watch 2 should be detected as the SAME roll
        is_same_1 = MultiWatchFusion.are_same_roll(run_1_w1, run_1_w2)
        self.assertTrue(is_same_1, "Watch 1 and Watch 2 from Roll 1 must be identified as the same roll")

        # Roll 1 Watch 1 & Roll 2 Watch 1 should NOT be detected as the same roll
        is_same_diff = MultiWatchFusion.are_same_roll(run_1_w1, run_2_w1)
        self.assertFalse(is_same_diff, "Roll 1 and Roll 2 must NOT be flagged as the same roll")
        print("\n[Test 2 Passed] Multi-watch same roll detection verified correctly!")

    def test_03_multi_watch_fusion(self):
        run_1_w1 = GarminParser.parse_file(self.file_r1_w1)
        run_1_w2 = GarminParser.parse_file(self.file_r1_w2)

        fused = MultiWatchFusion.fuse_runs([run_1_w1, run_1_w2])
        self.assertTrue(fused["is_fused"])
        self.assertEqual(fused["watch_count"], 2)
        self.assertGreater(len(fused["records"]), 50) # High-res spline records
        self.assertGreater(fused["max_speed_mph"], 20.0)
        print(f"\n[Test 3 Passed] Fused 2 watches into {len(fused['records'])} smooth points, max speed: {fused['max_speed_mph']} mph")

    def test_04_segmentation(self):
        run_1_w1 = GarminParser.parse_file(self.file_r1_w1)
        fused = MultiWatchFusion.fuse_runs([run_1_w1])

        segmenter = CourseSegmenter()
        segmented = segmenter.segment_roll(fused)
        segments = segmented["segments"]

        self.assertIn("hill2_drop", segments)
        self.assertIn("stop_sign_turn", segments)
        self.assertIn("monument_straight", segments)
        self.assertIn("chute_turn", segments)
        self.assertIn("chute_rollout", segments)

        chute_metrics = segments["chute_turn"]["metrics"]
        print(f"\n[Test 4 Passed] The Chute Metrics:")
        print(f"  Entry Speed: {chute_metrics['entry_speed_mph']} mph")
        print(f"  Apex Speed: {chute_metrics['apex_speed_mph']} mph")
        print(f"  Min Speed: {chute_metrics['min_speed_mph']} mph")
        print(f"  Exit Speed: {chute_metrics['exit_speed_mph']} mph")
        print(f"  Transit Time: {chute_metrics['transit_time_sec']} s")

        self.assertGreater(chute_metrics["entry_speed_mph"], 15.0)
        self.assertGreater(chute_metrics["exit_speed_mph"], 15.0)

    def test_05_notes_manager(self):
        notes_mgr = NotesManager()
        saved = notes_mgr.save_notes(
            roll_id="roll_test_1",
            driver_name="Sarah",
            buggy_name="Apex Firebird",
            general_notes="Felt good into the Chute, held higher line.",
            segment_notes={"chute_apex": "Minimum speed felt faster than run 1."}
        )
        loaded = notes_mgr.get_notes("roll_test_1")
        self.assertEqual(loaded["driver_name"], "Sarah")
        self.assertEqual(loaded["buggy_name"], "Apex Firebird")
        print("\n[Test 5 Passed] Driver notes saved and verified successfully!")


if __name__ == "__main__":
    unittest.main()
