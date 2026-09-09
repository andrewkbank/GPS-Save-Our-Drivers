"""
Multi-Watch Sensor Fusion and Roll Overlap Detector.
Detects when multiple Garmin watches recorded the same freeroll,
fuses multi-GPS fixes and Doppler velocity streams, and applies spline smoothing
to eliminate single-watch 3-5m positional error and deliver crisp telemetry.
"""

import math
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple, Optional
import numpy as np
from scipy.interpolate import UnivariateSpline
from scipy.signal import savgol_filter

from core.garmin_parser import haversine_distance, calculate_bearing


class MultiWatchFusion:
    """Manages multi-watch roll detection and sensor fusion."""

    @staticmethod
    def are_same_roll(
        run_a: Dict[str, Any],
        run_b: Dict[str, Any],
        min_overlap_sec: float = 12.0,
        max_mean_separation_m: float = 30.0
    ) -> bool:
        """
        Determine if two parsed activity runs represent the same freeroll
        by evaluating temporal overlap (GPS UTC) and spatial track proximity.
        """
        try:
            start_a = datetime.fromisoformat(run_a["start_time"])
            end_a = datetime.fromisoformat(run_a["end_time"])
            start_b = datetime.fromisoformat(run_b["start_time"])
            end_b = datetime.fromisoformat(run_b["end_time"])
        except (KeyError, ValueError):
            return False

        overlap_start = max(start_a, start_b)
        overlap_end = min(end_a, end_b)
        overlap_duration = (overlap_end - overlap_start).total_seconds()

        if overlap_duration < min_overlap_sec:
            return False

        # Spatial check during overlapping timestamps
        records_a = {r["timestamp"]: r for r in run_a["records"]}
        records_b = {r["timestamp"]: r for r in run_b["records"]}

        common_timestamps = set(records_a.keys()) & set(records_b.keys())
        if not common_timestamps:
            # Check near-time matches within +/- 1 second
            times_a = [(datetime.fromisoformat(r["timestamp"]), r) for r in run_a["records"]]
            times_b = [(datetime.fromisoformat(r["timestamp"]), r) for r in run_b["records"]]
            distances = []
            for t_a, r_a in times_a:
                for t_b, r_b in times_b:
                    if abs((t_a - t_b).total_seconds()) <= 1.0:
                        dist = haversine_distance(r_a["lat"], r_a["lon"], r_b["lat"], r_b["lon"])
                        distances.append(dist)
                        break
        else:
            distances = [
                haversine_distance(
                    records_a[ts]["lat"], records_a[ts]["lon"],
                    records_b[ts]["lat"], records_b[ts]["lon"]
                )
                for ts in common_timestamps
            ]

        if not distances:
            return False

        mean_dist = sum(distances) / len(distances)
        return mean_dist <= max_mean_separation_m

    @staticmethod
    def fuse_runs(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Fuse multiple GPS streams from the same roll into a single high-accuracy trajectory.
        Supports 1, 2, or 3 watches.
        """
        if not runs:
            raise ValueError("No runs provided for fusion")

        if len(runs) == 1:
            return MultiWatchFusion._smooth_single_run(runs[0])

        # Multi-watch fusion
        watch_ids = [r.get("device_id", f"watch_{i+1}") for i, r in enumerate(runs)]
        watch_names = [r.get("device_name", f"Watch {i+1}") for i, r in enumerate(runs)]

        # Find earliest start time as common reference
        all_pts: List[Dict[str, Any]] = []
        for run_idx, run in enumerate(runs):
            wid = watch_ids[run_idx]
            for pt in run["records"]:
                pt_time = datetime.fromisoformat(pt["timestamp"])
                all_pts.append({
                    "datetime": pt_time,
                    "lat": pt["lat"],
                    "lon": pt["lon"],
                    "speed_mps": pt["speed_mps"],
                    "speed_mph": pt["speed_mph"],
                    "altitude_m": pt.get("altitude_m", 0.0),
                    "watch_id": wid,
                    "watch_index": run_idx
                })

        # Sort all points chronologically
        all_pts.sort(key=lambda p: p["datetime"])
        base_time = all_pts[0]["datetime"]
        for p in all_pts:
            p["t_sec"] = (p["datetime"] - base_time).total_seconds()

        # Group points within 0.5s into time clusters for spatial & doppler fusion
        clustered_fused: List[Dict[str, Any]] = []
        i = 0
        while i < len(all_pts):
            cluster = [all_pts[i]]
            j = i + 1
            while j < len(all_pts) and (all_pts[j]["t_sec"] - all_pts[i]["t_sec"]) <= 0.6:
                cluster.append(all_pts[j])
                j += 1
            i = j

            # Calculate fused center and average doppler speed
            avg_lat = sum(p["lat"] for p in cluster) / len(cluster)
            avg_lon = sum(p["lon"] for p in cluster) / len(cluster)
            avg_speed_mps = sum(p["speed_mps"] for p in cluster) / len(cluster)
            avg_alt = sum(p["altitude_m"] for p in cluster) / len(cluster)
            t_mid = sum(p["t_sec"] for p in cluster) / len(cluster)
            sample_count = len(cluster)

            clustered_fused.append({
                "t_sec": t_mid,
                "lat": avg_lat,
                "lon": avg_lon,
                "speed_mps": avg_speed_mps,
                "speed_mph": avg_speed_mps * 2.23694,
                "altitude_m": avg_alt,
                "sample_count": sample_count
            })

        # Apply Spline & Savitzky-Golay smoothing on the fused points
        return MultiWatchFusion._apply_advanced_smoothing(
            clustered_fused,
            raw_runs=runs,
            is_fused=True
        )

    @staticmethod
    def _smooth_single_run(run: Dict[str, Any]) -> Dict[str, Any]:
        """Apply smoothing to a single-watch run."""
        records = run["records"]
        pts = [
            {
                "t_sec": r["elapsed_sec"],
                "lat": r["lat"],
                "lon": r["lon"],
                "speed_mps": r["speed_mps"],
                "speed_mph": r["speed_mph"],
                "altitude_m": r.get("altitude_m", 0.0),
                "sample_count": 1
            }
            for r in records
        ]
        return MultiWatchFusion._apply_advanced_smoothing(
            pts,
            raw_runs=[run],
            is_fused=False
        )

    @staticmethod
    def _apply_advanced_smoothing(
        pts: List[Dict[str, Any]],
        raw_runs: List[Dict[str, Any]],
        is_fused: bool
    ) -> Dict[str, Any]:
        """
        Convert to local Cartesian metric frame (x, y meters) for numerically stable
        cubic spline smoothing, then project back to lat/lon and compute clean telemetry.
        """
        n = len(pts)
        if n < 4:
            return {
                "is_fused": is_fused,
                "watch_count": len(raw_runs),
                "fused_records": pts,
                "raw_runs": raw_runs
            }

        t_arr = np.array([p["t_sec"] for p in pts], dtype=float)
        lat_arr = np.array([p["lat"] for p in pts], dtype=float)
        lon_arr = np.array([p["lon"] for p in pts], dtype=float)
        speed_arr = np.array([p["speed_mph"] for p in pts], dtype=float)

        # Local Cartesian projection origin (first point)
        lat0 = float(lat_arr[0])
        lon0 = float(lon_arr[0])
        cos_lat0 = math.cos(math.radians(lat0))

        # Convert to meters
        x_m = (lon_arr - lon0) * (111320.0 * cos_lat0)
        y_m = (lat_arr - lat0) * 110540.0

        # Ensure strictly increasing time
        for idx in range(1, len(t_arr)):
            if t_arr[idx] <= t_arr[idx - 1]:
                t_arr[idx] = t_arr[idx - 1] + 0.05

        try:
            # Expected GPS error variance in meters: ~2.0m for fused, ~3.5m for single
            sigma_m = 2.0 if is_fused else 3.5
            s_cartesian = float(n * (sigma_m ** 2) * 0.4)
            s_speed = float(n * 0.8)

            spline_x = UnivariateSpline(t_arr, x_m, s=s_cartesian)
            spline_y = UnivariateSpline(t_arr, y_m, s=s_cartesian)
            spline_speed = UnivariateSpline(t_arr, speed_arr, s=s_speed)

            # High-resolution uniform time points (0.25s interval)
            t_fine = np.linspace(t_arr[0], t_arr[-1], max(n * 4, 80))
            x_fine = spline_x(t_fine)
            y_fine = spline_y(t_fine)
            speed_fine = np.maximum(spline_speed(t_fine), 0.0)

            # Savitzky-Golay filter to ensure smooth acceleration
            window = min(11, len(speed_fine) - 1)
            if window % 2 == 0:
                window -= 1
            if window >= 5:
                speed_fine = savgol_filter(speed_fine, window_length=window, polyorder=2)
                speed_fine = np.maximum(speed_fine, 0.0)

            # Project back from meters to decimal degrees
            lat_fine = lat0 + (y_fine / 110540.0)
            lon_fine = lon0 + (x_fine / (111320.0 * cos_lat0))

            smoothed_records: List[Dict[str, Any]] = []
            cum_dist = 0.0

            for k in range(len(t_fine)):
                cur_lat = float(lat_fine[k])
                cur_lon = float(lon_fine[k])
                cur_speed_mph = float(speed_fine[k])
                cur_speed_mps = cur_speed_mph / 2.23694

                if k > 0:
                    prev_lat = smoothed_records[k - 1]["lat"]
                    prev_lon = smoothed_records[k - 1]["lon"]
                    d = haversine_distance(prev_lat, prev_lon, cur_lat, cur_lon)
                    cum_dist += d
                    bearing = calculate_bearing(prev_lat, prev_lon, cur_lat, cur_lon)
                    dt = t_fine[k] - t_fine[k - 1]
                    accel_g = ((cur_speed_mps - smoothed_records[k - 1]["speed_mps"]) / dt) / 9.81 if dt > 0 else 0.0
                else:
                    bearing = 0.0
                    accel_g = 0.0

                smoothed_records.append({
                    "elapsed_sec": round(float(t_fine[k]), 2),
                    "lat": round(cur_lat, 7),
                    "lon": round(cur_lon, 7),
                    "speed_mph": round(cur_speed_mph, 2),
                    "speed_mps": round(cur_speed_mps, 2),
                    "cum_dist_m": round(cum_dist, 2),
                    "bearing_deg": round(bearing, 1),
                    "accel_g": round(accel_g, 3)
                })

            if len(smoothed_records) > 1:
                smoothed_records[0]["bearing_deg"] = smoothed_records[1]["bearing_deg"]

        except Exception as e:
            smoothed_records = pts

        # Package results
        primary_run = raw_runs[0]
        roll_id = f"roll_{primary_run.get('start_time', 'unknown').replace(':', '-').replace('+', '_')}"

        return {
            "roll_id": roll_id,
            "is_fused": is_fused,
            "watch_count": len(raw_runs),
            "watch_devices": [
                {
                    "device_id": r.get("device_id", f"watch_{i+1}"),
                    "device_name": r.get("device_name", f"Garmin {i+1}"),
                    "source_file": r.get("source_file", ""),
                    "point_count": len(r.get("records", []))
                }
                for i, r in enumerate(raw_runs)
            ],
            "start_time": primary_run.get("start_time"),
            "end_time": primary_run.get("end_time"),
            "duration_sec": smoothed_records[-1]["elapsed_sec"] - smoothed_records[0]["elapsed_sec"] if smoothed_records else 0,
            "total_dist_m": smoothed_records[-1]["cum_dist_m"] if smoothed_records else 0,
            "max_speed_mph": round(max((r["speed_mph"] for r in smoothed_records), default=0.0), 2),
            "avg_speed_mph": round(sum(r["speed_mph"] for r in smoothed_records) / len(smoothed_records), 2) if smoothed_records else 0,
            "records": smoothed_records,
            "raw_watch_records": [
                {
                    "device_id": r.get("device_id", f"watch_{i+1}"),
                    "device_name": r.get("device_name", f"Garmin {i+1}"),
                    "records": r.get("records", [])
                }
                for i, r in enumerate(raw_runs)
            ]
        }
