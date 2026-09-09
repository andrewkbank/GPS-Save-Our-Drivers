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

        for seg in self.segments:
            seg_id = seg["id"]
            start_gate = seg["start_gate"]
            end_gate = seg["end_gate"]

            # Find closest record index to start_gate from curr_search_idx
            start_idx = self._find_closest_gate(records, start_gate, start_from=curr_search_idx)
            
            # Find closest record index to end_gate after start_idx
            search_end_from = max(start_idx + 1, curr_search_idx + 1)
            end_idx = self._find_closest_gate(records, end_gate, start_from=search_end_from)

            if end_idx <= start_idx or (end_idx - start_idx) < 1:
                # If forward search failed, try global search as fallback
                start_idx = self._find_closest_gate(records, start_gate, start_from=0)
                end_idx = self._find_closest_gate(records, end_gate, start_from=start_idx + 1)

            if end_idx <= start_idx:
                continue

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
                rel_apex_idx = self._find_closest_gate(slice_records, apex_gate, start_from=0)
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

        return {
            "roll_id": roll_data.get("roll_id", "unknown"),
            "segments": segmented_results
        }

    def _find_closest_gate(self, records: List[Dict[str, Any]], gate: Dict[str, Any], start_from: int = 0) -> int:
        """Find the index of the GPS record closest to the gate (using midpoint or gate line)."""
        if not records:
            return 0
        start_from = max(0, min(start_from, len(records) - 1))
        
        gate_lat = gate.get("lat")
        gate_lon = gate.get("lon")
        gate_line = gate.get("gate_line")

        min_dist = float("inf")
        best_idx = start_from

        for idx in range(start_from, len(records)):
            pt = records[idx]
            if gate_line and len(gate_line) >= 2:
                # Minimum distance to gate line endpoints and midpoint
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
