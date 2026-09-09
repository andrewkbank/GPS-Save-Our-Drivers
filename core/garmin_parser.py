"""
Garmin Activity Parser for GPS Save Our Drivers.
Parses .FIT (native Garmin binary) and .GPX files, extracts GPS coordinates,
timestamps, Doppler speed, altitude, and derives heading, bearing, and distance.
"""

import os
import math
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import fitparse
import gpxpy


def semicircles_to_degrees(semicircles: Optional[int]) -> Optional[float]:
    """Convert Garmin semicircles to decimal degrees."""
    if semicircles is None:
        return None
    return semicircles * (180.0 / 2**31)


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in meters between two lat/lon pairs."""
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def calculate_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate bearing from point 1 to point 2 in degrees [0, 360)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    bearing = math.degrees(math.atan2(y, x))
    return (bearing + 360.0) % 360.0


class GarminParser:
    """Parser for Garmin Forerunner 35 FIT and GPX activity records."""

    @staticmethod
    def parse_file(filepath: str) -> Dict[str, Any]:
        ext = os.path.splitext(filepath)[1].lower()
        if ext == ".fit":
            return GarminParser.parse_fit(filepath)
        elif ext == ".gpx":
            return GarminParser.parse_gpx(filepath)
        else:
            raise ValueError(f"Unsupported file format: {ext}. Must be .fit or .gpx")

    @staticmethod
    def parse_fit(filepath: str) -> Dict[str, Any]:
        """Parse Garmin binary .FIT file."""
        fitfile = fitparse.FitFile(filepath)
        records: List[Dict[str, Any]] = []
        device_serial = None
        device_name = "Garmin Forerunner 35"
        start_time = None

        # Extract device info if present
        for msg in fitfile.get_messages("device_info"):
            for data in msg:
                if data.name == "serial_number" and data.value is not None:
                    device_serial = str(data.value)
                if data.name == "product_name" and data.value:
                    device_name = str(data.value)

        # Extract record messages
        for msg in fitfile.get_messages("record"):
            record_dict: Dict[str, Any] = {}
            for data in msg:
                record_dict[data.name] = data.value

            lat = record_dict.get("position_lat")
            lon = record_dict.get("position_long")
            if lat is not None and lon is not None:
                deg_lat = semicircles_to_degrees(lat)
                deg_lon = semicircles_to_degrees(lon)
            else:
                continue

            ts = record_dict.get("timestamp")
            if ts is not None and isinstance(ts, datetime):
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if start_time is None:
                    start_time = ts
                t_sec = (ts - start_time).total_seconds()
                iso_ts = ts.isoformat()
            else:
                continue

            # Speed: Garmin records enhanced_speed or speed in m/s
            speed_mps = record_dict.get("enhanced_speed")
            if speed_mps is None:
                speed_mps = record_dict.get("speed", 0.0)
            if speed_mps is None:
                speed_mps = 0.0
            else:
                speed_mps = float(speed_mps)

            speed_mph = speed_mps * 2.23694

            alt = record_dict.get("enhanced_altitude")
            if alt is None:
                alt = record_dict.get("altitude", 0.0)
            alt = float(alt) if alt is not None else 0.0

            dist = record_dict.get("distance", 0.0)
            dist = float(dist) if dist is not None else 0.0

            records.append({
                "timestamp": iso_ts,
                "elapsed_sec": t_sec,
                "lat": round(deg_lat, 7),
                "lon": round(deg_lon, 7),
                "speed_mps": round(speed_mps, 3),
                "speed_mph": round(speed_mph, 2),
                "altitude_m": round(alt, 1),
                "garmin_dist_m": round(dist, 1)
            })

        return GarminParser._post_process_records(
            records=records,
            source_file=filepath,
            device_id=device_serial or "watch_default",
            device_name=device_name
        )

    @staticmethod
    def parse_gpx(filepath: str) -> Dict[str, Any]:
        """Parse standard GPX activity file."""
        with open(filepath, "r", encoding="utf-8") as f:
            gpx = gpxpy.parse(f)

        records: List[Dict[str, Any]] = []
        start_time = None

        for track in gpx.tracks:
            for segment in track.segments:
                for pt in segment.points:
                    if pt.time is None:
                        continue
                    ts = pt.time
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if start_time is None:
                        start_time = ts
                    t_sec = (ts - start_time).total_seconds()

                    spd_mps = pt.speed
                    if spd_mps is None:
                        # Check extensions for speed
                        for ext in pt.extensions:
                            if "speed" in ext.tag.lower() and ext.text:
                                try:
                                    spd_mps = float(ext.text)
                                    break
                                except ValueError:
                                    pass

                    spd_mps = float(spd_mps) if spd_mps is not None else 0.0

                    records.append({
                        "timestamp": ts.isoformat(),
                        "elapsed_sec": t_sec,
                        "lat": round(pt.latitude, 7),
                        "lon": round(pt.longitude, 7),
                        "speed_mps": round(spd_mps, 3),
                        "speed_mph": round(spd_mps * 2.23694, 2),
                        "altitude_m": round(pt.elevation, 1) if pt.elevation is not None else 0.0,
                        "garmin_dist_m": 0.0
                    })

        return GarminParser._post_process_records(
            records=records,
            source_file=filepath,
            device_id="gpx_import",
            device_name="GPX Track"
        )

    @staticmethod
    def _post_process_records(
        records: List[Dict[str, Any]],
        source_file: str,
        device_id: str,
        device_name: str
    ) -> Dict[str, Any]:
        """Compute cumulative distance, bearings, and basic filtering."""
        if not records:
            return {
                "source_file": source_file,
                "device_id": device_id,
                "device_name": device_name,
                "point_count": 0,
                "records": []
            }

        cum_dist = 0.0
        records[0]["cum_dist_m"] = 0.0
        records[0]["bearing_deg"] = 0.0

        for i in range(1, len(records)):
            prev = records[i - 1]
            curr = records[i]
            d = haversine_distance(prev["lat"], prev["lon"], curr["lat"], curr["lon"])
            cum_dist += d
            curr["cum_dist_m"] = round(cum_dist, 2)
            b = calculate_bearing(prev["lat"], prev["lon"], curr["lat"], curr["lon"])
            curr["bearing_deg"] = round(b, 1)

            # If speed wasn't natively supplied in record, calculate finite-diff speed
            dt = curr["elapsed_sec"] - prev["elapsed_sec"]
            if curr["speed_mps"] == 0.0 and dt > 0:
                calc_speed = d / dt
                curr["speed_mps"] = round(calc_speed, 3)
                curr["speed_mph"] = round(calc_speed * 2.23694, 2)

        records[0]["bearing_deg"] = records[1]["bearing_deg"] if len(records) > 1 else 0.0

        return {
            "source_file": os.path.basename(source_file),
            "full_path": os.path.abspath(source_file),
            "device_id": device_id,
            "device_name": device_name,
            "start_time": records[0]["timestamp"],
            "end_time": records[-1]["timestamp"],
            "duration_sec": records[-1]["elapsed_sec"] - records[0]["elapsed_sec"],
            "total_dist_m": round(cum_dist, 2),
            "max_speed_mph": round(max(r["speed_mph"] for r in records), 2),
            "avg_speed_mph": round(sum(r["speed_mph"] for r in records) / len(records), 2),
            "point_count": len(records),
            "records": records
        }
