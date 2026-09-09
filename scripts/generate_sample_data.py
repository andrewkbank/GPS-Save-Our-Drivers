"""
Sample GPS Data Generator for CMU Buggy Freeroll.
Generates realistic 1Hz Garmin GPX files with Doppler speed records
simulating 2 Garmin watches mounted on a buggy during a freeroll practice.
Creates two consecutive rolls with different driving lines into the Chute
to demonstrate multi-watch detection, fusion, segment isolation, and run comparison.
"""

import os
import math
import random
from datetime import datetime, timezone, timedelta


def haversine_dist(lat1, lon1, lat2, lon2):
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def generate_buggy_sample_files(raw_dir: str):
    os.makedirs(raw_dir, exist_ok=True)

    # Key course gates (lat, lon, target_speed_mph)
    base_waypoints = [
        # Gate 1: End of Hill 2 (Freeroll start pusher release)
        (40.440172, -79.942656, 12.0),
        # Hill 2 Drop down Schenley Dr
        (40.439946, -79.943274, 18.0),
        (40.439480, -79.943727, 24.5),
        (40.439011, -79.944211, 29.0),
        (40.438702, -79.945267, 32.5),
        # Gate 2: Start of Stop Sign Turn
        (40.438666, -79.945526, 31.0),
        (40.438733, -79.946019, 27.5),
        # Gate 3: End of Stop Sign Turn
        (40.438998, -79.946529, 26.5),
        # Monument Straightaway down Schenley Dr
        (40.439613, -79.946775, 29.5),
        (40.440037, -79.946855, 33.0),
        (40.440359, -79.947250, 36.5),
        # Gate 4: Start of Chute Entry
        (40.440584, -79.947799, 31.5),
        # Chute Apex
        (40.440870, -79.948087, 23.5),
        # Gate 5: Chute Exit onto Frew St
        (40.440990, -79.947952, 25.0),
        # Frew St Rollout
        (40.441188, -79.947677, 23.5),
        (40.441367, -79.947461, 20.5),
        # Gate 6: Hill 3 Pickup / End of Freeroll
        (40.441454, -79.946828, 17.0)
    ]

    base_date = datetime(2026, 9, 8, 8, 30, 0, tzinfo=timezone.utc)

    # Roll 1: Standard Line (Chute apex 23.5 mph, exit 25.0 mph)
    _create_roll_watches(
        raw_dir=raw_dir,
        roll_num=1,
        start_time=base_date,
        waypoints=base_waypoints,
        speed_factor=1.0,
        track_name="Apex Firebird - Roll 1 (Standard Line)"
    )

    # Roll 2: Aggressive High Line into Chute (+2.5 mph exit speed)
    alt_waypoints = list(base_waypoints)
    alt_waypoints[11] = (40.440590, -79.947810, 32.5) # wider entry
    alt_waypoints[12] = (40.440875, -79.948092, 25.5) # higher apex speed (25.5 mph vs 23.5 mph)
    alt_waypoints[13] = (40.440995, -79.947948, 27.5) # higher exit speed (27.5 mph vs 25.0 mph)
    alt_waypoints[14] = (40.441190, -79.947670, 25.0)

    _create_roll_watches(
        raw_dir=raw_dir,
        roll_num=2,
        start_time=base_date + timedelta(minutes=20),
        waypoints=alt_waypoints,
        speed_factor=1.03,
        track_name="Apex Firebird - Roll 2 (High Line)"
    )

    print("Sample rolls generated successfully in:", raw_dir)


def _create_roll_watches(raw_dir, roll_num, start_time, waypoints, speed_factor, track_name):
    random.seed(100 + roll_num)

    # Calculate cumulative distance along waypoints
    distances = [0.0]
    for i in range(len(waypoints) - 1):
        d = haversine_dist(waypoints[i][0], waypoints[i][1], waypoints[i+1][0], waypoints[i+1][1])
        distances.append(distances[-1] + d)
    total_dist = distances[-1]

    # Time integration with ~1.0 second steps
    current_dist = 0.0
    current_time_sec = 0.0
    times = [0.0]
    traj = []

    # Interpolate function
    def interpolate_at_dist(dist_m):
        if dist_m <= 0:
            return waypoints[0][0], waypoints[0][1], waypoints[0][2] * speed_factor
        if dist_m >= total_dist:
            return waypoints[-1][0], waypoints[-1][1], waypoints[-1][2] * speed_factor
        for idx in range(len(distances) - 1):
            if distances[idx] <= dist_m <= distances[idx + 1]:
                seg_len = distances[idx + 1] - distances[idx]
                frac = (dist_m - distances[idx]) / seg_len if seg_len > 0 else 0.0
                lat = waypoints[idx][0] + frac * (waypoints[idx + 1][0] - waypoints[idx][0])
                lon = waypoints[idx][1] + frac * (waypoints[idx + 1][1] - waypoints[idx][1])
                spd = (waypoints[idx][2] + frac * (waypoints[idx + 1][2] - waypoints[idx][2])) * speed_factor
                return lat, lon, spd
        return waypoints[-1][0], waypoints[-1][1], waypoints[-1][2] * speed_factor

    while current_dist < total_dist:
        lat, lon, spd_mph = interpolate_at_dist(current_dist)
        traj.append((current_time_sec, current_dist, lat, lon, spd_mph))
        spd_mps = spd_mph / 2.23694
        current_dist += spd_mps * 1.0
        current_time_sec += 1.0

    # Append final point
    lat, lon, spd_mph = interpolate_at_dist(total_dist)
    traj.append((current_time_sec, total_dist, lat, lon, spd_mph))

    # Build XML for Watch 1 and Watch 2
    def build_gpx_xml(watch_name, clock_offset_sec, noise_seed):
        rnd = random.Random(noise_seed)
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<gpx version="1.1" creator="Garmin Forerunner 35" xmlns="http://www.topografix.com/GPX/1/1">',
            '  <trk>',
            f'    <name>{track_name} - {watch_name}</name>',
            '    <trkseg>'
        ]

        for t_sec, dist_m, b_lat, b_lon, b_spd in traj:
            # 1.5 - 2.5m GPS noise
            noise_lat = rnd.gauss(0, 0.000018)
            noise_lon = rnd.gauss(0, 0.000018)
            noise_spd = rnd.gauss(0, 0.25)

            pt_time = start_time + timedelta(seconds=t_sec + clock_offset_sec)
            iso_time = pt_time.strftime("%Y-%m-%dT%H:%M:%SZ")
            pt_lat = b_lat + noise_lat
            pt_lon = b_lon + noise_lon
            pt_spd_mps = max((b_spd + noise_spd) / 2.23694, 0.5)
            pt_alt = 272.0 - (dist_m / total_dist) * 35.0

            lines.append(f'      <trkpt lat="{pt_lat:.7f}" lon="{pt_lon:.7f}">')
            lines.append(f'        <ele>{pt_alt:.1f}</ele>')
            lines.append(f'        <time>{iso_time}</time>')
            lines.append(f'        <speed>{pt_spd_mps:.3f}</speed>')
            lines.append('      </trkpt>')

        lines.extend([
            '    </trkseg>',
            '  </trk>',
            '</gpx>'
        ])
        return '\n'.join(lines)

    file_a = os.path.join(raw_dir, f"roll_{roll_num}_watch1_garmin35.gpx")
    file_b = os.path.join(raw_dir, f"roll_{roll_num}_watch2_garmin35.gpx")

    with open(file_a, "w", encoding="utf-8") as f:
        f.write(build_gpx_xml("Watch 1 (Left)", 0.0, 100 + roll_num))

    with open(file_b, "w", encoding="utf-8") as f:
        f.write(build_gpx_xml("Watch 2 (Right)", 0.35, 200 + roll_num))


if __name__ == "__main__":
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    generate_buggy_sample_files(os.path.join(base, "data", "raw"))
