#!/usr/bin/env python3
"""Rebuild rec.manager + total.info for the generated hike record (count = 1).

The record root mirrors the reference layout at /home/hgw/shaolu/sport_record:
  rec.manager   sport_record_manager_t : count(u16) + reserved(u16) + 100 x 20B
  total.info    sport_record_total_info_t : distance/calories/count/time + reserved
"""

import struct
from pathlib import Path
from statistics import mean

from generate_native_hike_record import (
    TRACK_ID, DURATION_S, SPORT_TYPE_OUTDOOR_HIKING, make_heart_rates,
    route_distance, resample, ROUTE, OUT,
)
from generate_outdoor_hike_gpx import ROUTE as _R  # noqa: F401  (resample import side)


def compute_stats():
    points = resample(ROUTE, 61)
    points[0] = (118.741050, 32.000300)
    points[-1] = (118.721361, 31.966454)
    distance = round(route_distance(points))
    calories = round(distance * 0.07)
    return distance, calories


def manager_bytes(distance, calories):
    data = bytearray(2004)
    struct.pack_into("<HH", data, 0, 1, 0)          # count=1, reserved=0
    record = struct.pack(
        "<BbHI f I BBBB",
        0x11,                  # sync: NOT_SYNCED(1) | multi: NOT_MULTI_SPORTS(1)
        32,                    # Asia/Shanghai, quarter-hour units
        SPORT_TYPE_OUTDOOR_HIKING,
        DURATION_S,
        float(distance),
        TRACK_ID,
        0,                     # SPORT_START_TYPE_NORMAL
        0,                     # dis_unit: meters
        0, 0,                  # reserved[2]
    )
    data[4:24] = record
    return bytes(data)


def total_bytes(distance, calories):
    data = bytearray(64)
    struct.pack_into("<IIII", data, 0, distance, calories, 1, DURATION_S)
    return bytes(data)


def main():
    distance, calories = compute_stats()
    root = Path("configs")
    (root / "rec.manager").write_bytes(manager_bytes(distance, calories))
    (root / "total.info").write_bytes(total_bytes(distance, calories))
    print(f"rec.manager: 1 entry (type={SPORT_TYPE_OUTDOOR_HIKING} "
          f"track={TRACK_ID} time={DURATION_S}s dist={distance}m cal={calories})")
    print(f"total.info: distance={distance} calories={calories} count=1 time={DURATION_S}")


if __name__ == "__main__":
    main()
