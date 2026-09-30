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

            # ── Find the gate crossings, returning (index_before, t) ──────────
            start_i, start_t = self._find_gate_crossing_param(
                records, start_gate, start_from=curr_search_idx
            )

            search_end_from = max(start_i + 1, curr_search_idx + 1)
            end_i, end_t = self._find_gate_crossing_param(
                records, end_gate, start_from=search_end_from
            )

            if end_i <= start_i:
                # Forward search failed — retry globally
                start_i, start_t = self._find_gate_crossing_param(
                    records, start_gate, start_from=0
                )
                end_i, end_t = self._find_gate_crossing_param(
                    records, end_gate, start_from=start_i + 1
                )

            if end_i <= start_i:
                continue

            # ── Build synthetic boundary records via sub-sample interpolation ─
            # start_pt: interpolated record at the exact gate-line crossing
            # end_pt:   interpolated record at the exact gate-line crossing
            if start_t > 0.0 and start_i + 1 < len(records):
                start_pt = self._interpolate_record(
                    records[start_i], records[start_i + 1], start_t
                )
            else:
                start_pt = dict(records[start_i])

            if end_t > 0.0 and end_i + 1 < len(records):
                end_pt = self._interpolate_record(
                    records[end_i], records[end_i + 1], end_t
                )
            else:
                end_pt = dict(records[end_i])

            # ── Track outermost crossings for full-course crop ────────────────
            # Use start_i for indexing; the synthetic start_pt anchors time/dist.
            if course_start_idx is None:
                course_start_idx = start_i
            course_end_idx = max(course_end_idx, end_i)

            # Update search cursor for subsequent segments
            curr_search_idx = max(curr_search_idx, start_i)

            # ── Build slice: synthetic start + interior samples + synthetic end ─
            # Interior = records strictly between the two crossings
            interior = records[start_i + 1 : end_i + 1]
            slice_records = [start_pt] + [dict(r) for r in interior] + [end_pt]

            # ── Re-zero segment distance and elapsed time ─────────────────────
            seg_rebased_records: List[Dict[str, Any]] = []
            base_dist = start_pt["cum_dist_m"]
            base_time = start_pt["elapsed_sec"]

            for r in slice_records:
                r_copy = dict(r)
                r_copy["seg_dist_m"]  = round(r["cum_dist_m"]  - base_dist, 4)
                r_copy["seg_time_sec"] = round(r["elapsed_sec"] - base_time, 4)
                seg_rebased_records.append(r_copy)

            # ── Metric extraction using interpolated boundary values ───────────
            v_in  = start_pt["speed_mph"]
            v_out = end_pt["speed_mph"]
            v_min = min(r["speed_mph"] for r in slice_records)
            v_max = max(r["speed_mph"] for r in slice_records)

            # Average speed = total_distance / total_time, converted to mph.
            # We deliberately do NOT average the GPS device's speed_mph field — that
            # field is a Kalman-filtered estimate with a temporal lag that can read
            # 1-2 mph below the true position-derived speed (especially during
            # deceleration). distance and transit_time come from cum_dist_m /
            # elapsed_sec which are the GPS position ground-truth, so this definition
            # is always consistent with the displayed transit_time and distance values.
            MPS_TO_MPH = 2.23694
            transit_time = end_pt["elapsed_sec"] - start_pt["elapsed_sec"]
            distance     = end_pt["cum_dist_m"]  - start_pt["cum_dist_m"]
            v_avg = (distance / transit_time * MPS_TO_MPH) if transit_time > 0 else v_in

            # Check if segment defines a dedicated apex gate
            apex_gate = seg.get("apex_gate")
            if apex_gate:
                rel_apex_idx = self._find_gate_crossing(slice_records, apex_gate, start_from=0)
                v_apex = slice_records[rel_apex_idx]["speed_mph"]
            else:
                v_apex = v_min

            delta_v = v_out - v_in

            heading_in    = start_pt.get("bearing_deg", 0.0)
            heading_out   = end_pt.get("bearing_deg", 0.0)
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
                    "transit_time_sec": round(transit_time, 4),
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

    @staticmethod
    def _line_intersection_t(
        p1_lat: float, p1_lon: float,
        p2_lat: float, p2_lon: float,
        q1_lat: float, q1_lon: float,
        q2_lat: float, q2_lon: float,
    ) -> float:
        """
        Return the parametric parameter t ∈ [0, 1] along the GPS step p1→p2
        where it intersects the gate line q1→q2.

        Uses the standard 2-D line-segment intersection formula treating
        lat/lon as flat Cartesian coordinates (valid at these distances).
        Returns 0.5 as a safe fallback if the lines are nearly parallel.
        """
        # Direction vectors
        dx_p = p2_lat - p1_lat
        dy_p = p2_lon - p1_lon
        dx_q = q2_lat - q1_lat
        dy_q = q2_lon - q1_lon

        denom = dx_p * dy_q - dy_p * dx_q
        if abs(denom) < 1e-15:  # parallel / collinear — return midpoint
            return 0.5

        dx_start = q1_lat - p1_lat
        dy_start = q1_lon - p1_lon
        t = (dx_start * dy_q - dy_start * dx_q) / denom
        return max(0.0, min(1.0, t))  # clamp to [0, 1]

    @staticmethod
    def _interpolate_record(
        r1: Dict[str, Any],
        r2: Dict[str, Any],
        t: float,
    ) -> Dict[str, Any]:
        """
        Linearly interpolate a synthetic GPS record between r1 (t=0) and r2 (t=1).

        Scalar fields blended: lat, lon, elapsed_sec, cum_dist_m, speed_mph,
        bearing_deg (shortest-path wrap), and any other numeric fields copied
        from r1.  The record is flagged as interpolated for downstream consumers.
        """
        def lerp(a, b):
            return a + (b - a) * t

        def lerp_angle(a, b):
            """Lerp angles handling the 0°/360° wrap-around."""
            diff = ((b - a) + 180.0) % 360.0 - 180.0
            return (a + diff * t) % 360.0

        result = dict(r1)  # start from r1, inherit non-numeric fields
        result["lat"]         = lerp(r1["lat"],         r2["lat"])
        result["lon"]         = lerp(r1["lon"],         r2["lon"])
        result["elapsed_sec"] = lerp(r1["elapsed_sec"], r2["elapsed_sec"])
        result["cum_dist_m"]  = lerp(r1["cum_dist_m"],  r2["cum_dist_m"])
        result["speed_mph"]   = lerp(r1["speed_mph"],   r2["speed_mph"])
        if "bearing_deg" in r1 and "bearing_deg" in r2:
            result["bearing_deg"] = lerp_angle(r1["bearing_deg"], r2["bearing_deg"])
        result["_interpolated"] = True  # diagnostic flag
        return result

    def _find_gate_crossing(
        self,
        records: List[Dict[str, Any]],
        gate: Dict[str, Any],
        start_from: int = 0,
    ) -> int:
        """
        Thin wrapper — returns just the index of the record *after* the crossing.
        Used for apex_gate lookups and other places that only need an index.
        """
        idx, _t = self._find_gate_crossing_param(records, gate, start_from)
        return idx

    def _find_gate_crossing_param(
        self,
        records: List[Dict[str, Any]],
        gate: Dict[str, Any],
        start_from: int = 0,
    ) -> tuple:
        """
        Return ``(i, t)`` where ``i`` is the index of the record *before* the
        gate crossing and ``t ∈ [0, 1]`` is the parametric position along the
        step ``records[i] → records[i+1]`` at which the gate is crossed.

        The synthetic crossing point can then be computed with
        ``_interpolate_record(records[i], records[i+1], t)``.

        Falls back to ``(closest_idx, 0.0)`` when no clean segment crossing is
        found (e.g. noisy/short tracks that never cleanly pass through the gate).
        """
        if not records:
            return 0, 0.0
        start_from = max(0, min(start_from, len(records) - 1))

        gate_line = gate.get("gate_line", [])
        if len(gate_line) < 2:
            # No gate line defined — fall back to closest point
            return self._find_closest_point_to_gate(records, gate, start_from), 0.0

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
                t = self._line_intersection_t(
                    p1["lat"], p1["lon"],
                    p2["lat"], p2["lon"],
                    q1_lat, q1_lon,
                    q2_lat, q2_lon,
                )
                return i, t

        # Fallback: closest point to gate midpoint within search window
        return self._find_closest_point_to_gate(records, gate, start_from), 0.0

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
