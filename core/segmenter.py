"""
Course Segmenter for GPS Save Our Drivers.
Isolates individual curves and straightaways of the Schenley Park freeroll course,
extracting isolated trajectory slices and key actionable driver metrics:
entry speed (v_in), apex speed (v_min), exit speed (v_out), transit time (dt),
and re-zeroed distance profiles for direct run overlay.
"""

import json
import os
import math
from typing import List, Dict, Any, Optional

from core.garmin_parser import haversine_distance


class CourseSegmenter:
    """Segments freeroll telemetry into isolated sections."""

    def __init__(self, segments_config_path: Optional[str] = None):
        if segments_config_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            segments_config_path = os.path.join(base_dir, "config", "course_segments.json")

        self.config_path = segments_config_path
        self.course_data = self._load_config()
        self.segments = self.course_data.get("segments", [])

    def _load_config(self) -> Dict[str, Any]:
        if os.path.exists(self.config_path):
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"segments": []}

    def segment_roll(self, roll_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Slice a roll's records into isolated segments and calculate segment performance metrics.
        Works for both fused multi-watch runs and single-watch runs.
        """
        records = roll_data.get("records", [])
        if not records or len(records) < 4:
            return {
                "roll_id": roll_data.get("roll_id", "unknown"),
                "segmented_data": {}
            }

        segmented_results: Dict[str, Dict[str, Any]] = {}
        curr_search_idx = 0
        course_start_idx: Optional[int] = None  # index of first gate crossing across all segments
        course_end_idx: int = 0                  # index of last gate crossing across all segments

        for seg in self.segments:
            seg_id = seg["id"]
            start_gate = seg["start_gate"]
            end_gate = seg["end_gate"]

            # Find the first GPS record where the track crosses the start gate (finite)
            start_idx = self._find_gate_crossing(records, start_gate, start_from=curr_search_idx)

            # Find the first GPS record where the track crosses the end gate after start
            search_end_from = max(start_idx + 1, curr_search_idx + 1)
            end_idx = self._find_gate_crossing(records, end_gate, start_from=search_end_from)

            if end_idx <= start_idx or (end_idx - start_idx) < 1:
                # If forward search failed, retry with global search as fallback
                start_idx = self._find_gate_crossing(records, start_gate, start_from=0)
                end_idx = self._find_gate_crossing(records, end_gate, start_from=start_idx + 1)

            if end_idx <= start_idx:
                continue

            # Track the outermost crossing indices for the full-course crop
            if course_start_idx is None:
                course_start_idx = start_idx
            course_end_idx = max(course_end_idx, end_idx)

            # Update search cursor for subsequent segments
            curr_search_idx = max(curr_search_idx, start_idx)

            slice_records = records[start_idx : end_idx + 1]

            # Re-zero segment distance and elapsed time so two runs can be directly overlaid!
            seg_rebased_records: List[Dict[str, Any]] = []
            base_dist = slice_records[0]["cum_dist_m"]
            base_time = slice_records[0]["elapsed_sec"]

            for r in slice_records:
                r_copy = dict(r)
                r_copy["seg_dist_m"] = round(r["cum_dist_m"] - base_dist, 2)
                r_copy["seg_time_sec"] = round(r["elapsed_sec"] - base_time, 2)
                seg_rebased_records.append(r_copy)

            # Metric extraction
            v_in = slice_records[0]["speed_mph"]
            v_out = slice_records[-1]["speed_mph"]
            v_min = min(r["speed_mph"] for r in slice_records)
            v_max = max(r["speed_mph"] for r in slice_records)
            v_avg = sum(r["speed_mph"] for r in slice_records) / len(slice_records)

            # Check if segment defines a dedicated apex gate
            apex_gate = seg.get("apex_gate")
            if apex_gate:
                rel_apex_idx = self._find_gate_crossing(slice_records, apex_gate, start_from=0)
                v_apex = slice_records[rel_apex_idx]["speed_mph"]
            else:
                v_apex = v_min

            transit_time = slice_records[-1]["elapsed_sec"] - slice_records[0]["elapsed_sec"]
            distance = slice_records[-1]["cum_dist_m"] - slice_records[0]["cum_dist_m"]
            delta_v = v_out - v_in

            heading_in = slice_records[0].get("bearing_deg", 0.0)
            heading_out = slice_records[-1].get("bearing_deg", 0.0)
            heading_delta = (heading_out - heading_in + 180.0) % 360.0 - 180.0

            segmented_results[seg_id] = {
                "segment_id": seg_id,
                "name": seg["name"],
                "description": seg["description"],
                "color": seg["color"],
                "bad_gps": seg.get("bad_gps", False),
                "metrics": {
                    "entry_speed_mph": round(v_in, 2),
                    "min_speed_mph": round(v_min, 2),
                    "apex_speed_mph": round(v_apex, 2),
                    "exit_speed_mph": round(v_out, 2),
                    "max_speed_mph": round(v_max, 2),
                    "delta_speed_mph": round(delta_v, 2),
                    "avg_speed_mph": round(v_avg, 2),
                    "transit_time_sec": round(transit_time, 2),
                    "distance_m": round(distance, 2),
                    "heading_in_deg": round(heading_in, 1),
                    "heading_out_deg": round(heading_out, 1),
                    "heading_delta_deg": round(heading_delta, 1)
                },
                "point_count": len(seg_rebased_records),
                "records": seg_rebased_records
            }

        # Build a cropped, re-zeroed slice spanning first gate → last gate.
        # Falls back to the full records if no gate was ever crossed.
        if course_start_idx is not None and course_end_idx > course_start_idx:
            course_slice = records[course_start_idx: course_end_idx + 1]
            base_dist  = course_slice[0]["cum_dist_m"]
            base_time  = course_slice[0]["elapsed_sec"]
            course_records = []
            for r in course_slice:
                rc = dict(r)
                rc["seg_dist_m"]  = round(r["cum_dist_m"]  - base_dist, 2)
                rc["seg_time_sec"] = round(r["elapsed_sec"] - base_time, 2)
                course_records.append(rc)
        else:
            course_records = records  # no gate crossed → show everything

        return {
            "roll_id": roll_data.get("roll_id", "unknown"),
            "segments": segmented_results,
            "course_records": course_records
        }

    @staticmethod
    def _segments_intersect(
        p1_lat: float, p1_lon: float,
        p2_lat: float, p2_lon: float,
        q1_lat: float, q1_lon: float,
        q2_lat: float, q2_lon: float,
    ) -> bool:
        """
        Return True if the FINITE line segment p1→p2 (GPS track step) crosses
        the FINITE line segment q1→q2 (gate line).

        Uses the signed-area / cross-product test.  Lat/lon are treated as flat
        2-D coordinates — valid for the short distances involved here.
        """
        def cross(ax, ay, bx, by, cx, cy) -> float:
            """Signed area of triangle (A,B,C)."""
            return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)

        d1 = cross(q1_lat, q1_lon, q2_lat, q2_lon, p1_lat, p1_lon)
        d2 = cross(q1_lat, q1_lon, q2_lat, q2_lon, p2_lat, p2_lon)
        d3 = cross(p1_lat, p1_lon, p2_lat, p2_lon, q1_lat, q1_lon)
        d4 = cross(p1_lat, p1_lon, p2_lat, p2_lon, q2_lat, q2_lon)

        if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and \
           ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
            return True

        # Collinear / endpoint-touching cases — treat as a crossing
        def on_segment(ax, ay, bx, by, cx, cy) -> bool:
            return (min(ax, bx) <= cx <= max(ax, bx) and
                    min(ay, by) <= cy <= max(ay, by))

        if d1 == 0 and on_segment(q1_lat, q1_lon, q2_lat, q2_lon, p1_lat, p1_lon):
            return True
        if d2 == 0 and on_segment(q1_lat, q1_lon, q2_lat, q2_lon, p2_lat, p2_lon):
            return True
        if d3 == 0 and on_segment(p1_lat, p1_lon, p2_lat, p2_lon, q1_lat, q1_lon):
            return True
        if d4 == 0 and on_segment(p1_lat, p1_lon, p2_lat, p2_lon, q2_lat, q2_lon):
            return True

        return False

    def _find_gate_crossing(
        self,
        records: List[Dict[str, Any]],
        gate: Dict[str, Any],
        start_from: int = 0,
    ) -> int:
        """
        Return the index of the GPS record where the track first crosses the
        *finite* gate line segment, searching from start_from onward.

        The check is pair-wise: for each consecutive pair (records[i], records[i+1])
        the track micro-segment is tested against the gate line.  The index
        returned is i+1 (the record just after the crossing).

        Falls back to closest-point if no finite crossing is found (e.g. short
        or noisy track that never cleanly passes through the gate).
        """
        if not records:
            return 0
        start_from = max(0, min(start_from, len(records) - 1))

        gate_line = gate.get("gate_line", [])
        if len(gate_line) < 2:
            # No gate line defined — fall back to closest point
            return self._find_closest_point_to_gate(records, gate, start_from)

        q1_lat = gate_line[0]["lat"]
        q1_lon = gate_line[0]["lon"]
        q2_lat = gate_line[1]["lat"]
        q2_lon = gate_line[1]["lon"]

        for i in range(start_from, len(records) - 1):
            p1 = records[i]
            p2 = records[i + 1]
            if self._segments_intersect(
                p1["lat"], p1["lon"],
                p2["lat"], p2["lon"],
                q1_lat, q1_lon,
                q2_lat, q2_lon,
            ):
                return i + 1  # Record immediately after crossing

        # Fallback: closest point to gate midpoint within search window
        return self._find_closest_point_to_gate(records, gate, start_from)

    def _find_closest_point_to_gate(
        self,
        records: List[Dict[str, Any]],
        gate: Dict[str, Any],
        start_from: int = 0,
    ) -> int:
        """Closest-point fallback: find the record nearest the gate midpoint."""
        gate_lat = gate.get("lat")
        gate_lon = gate.get("lon")
        gate_line = gate.get("gate_line", [])
        start_from = max(0, min(start_from, len(records) - 1))

        min_dist = float("inf")
        best_idx = start_from

        for idx in range(start_from, len(records)):
            pt = records[idx]
            if gate_line and len(gate_line) >= 2:
                d1 = haversine_distance(pt["lat"], pt["lon"], gate_line[0]["lat"], gate_line[0]["lon"])
                d2 = haversine_distance(pt["lat"], pt["lon"], gate_line[1]["lat"], gate_line[1]["lon"])
                dm = haversine_distance(pt["lat"], pt["lon"], gate_lat, gate_lon)
                d = min(d1, d2, dm)
            else:
                d = haversine_distance(pt["lat"], pt["lon"], gate_lat, gate_lon)

            if d < min_dist:
                min_dist = d
                best_idx = idx

        return best_idx

    def _find_closest_point(self, records: List[Dict[str, Any]], lat: float, lon: float) -> int:
        """Find the index of the GPS record closest to the given target coordinate."""
        min_dist = float("inf")
        best_idx = 0
        for idx, pt in enumerate(records):
            d = haversine_distance(pt["lat"], pt["lon"], lat, lon)
            if d < min_dist:
                min_dist = d
                best_idx = idx
        return best_idx
