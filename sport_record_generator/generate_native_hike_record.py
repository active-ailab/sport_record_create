#!/usr/bin/env python3
"""Generate a native sport-record directory matching sport_record_save.c.

Produces the full set of files the watch's record module writes for one
outdoor-hiking activity:
  summary.data  - protobuf SportSummaryMessage (2-byte prefix + body)
  detail.data   - TLV node stream (timestamp/GPS/event/HR/pace/sec-level/laps)
  gps.data      - gps_detail_data_t records {u64 ts, i32 lng*3e6, i32 lat*3e6}
  event.data    - event_detail_data_t records {u64 ts, u64 event}
  lap.data      - lap_detail_data_t records {u64 ts, sport_node_lab_group_t + pad}
  hr.chart      - sport_Chart_data_t (24B) + hr.data (1200 x int16 chart pos)
  speed.chart   - sport_Chart_data_t (24B) + speed.data (1200 x int16 chart pos)
  special.data  - empty
  child.manager - child_sports_manager_t (504B)
"""

import math
import random
import struct
import uuid
from pathlib import Path
from statistics import mean

from generate_outdoor_hike_gpx import ROUTE, make_heart_rates, resample


TRACK_ID = 1786086000                 # 2026-08-07 15:00:00 Asia/Shanghai (UTC+8)
DURATION_S = 3600                     # 15:00 - 16:00
SPORT_TYPE_OUTDOOR_HIKING = 15        # SPORT_TYPE_OUTDOOR_HIKING
CHART_WIDTH = 1200                    # SPORT_RECORD_CHART_WIDTH
CHART_HEIGHT = 1000                   # SPORT_CHART_HEIGHT_FULL_PERCENT
CAL_PER_M = 0.07                      # ~430 kcal for 6.1 km hike
STRIDE_M = 0.75                       # avg step length (m)
EQUIP_ID = "8466129a-d6c2-4df8-a5c9-f60109713faa"   # watch device UUID (from reference)
OUT = Path("configs") / str(TRACK_ID) / str(TRACK_ID)


# ---------------------------------------------------------------- protobuf
def varint(value):
    value = int(value)
    result = bytearray()
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value & 0x7F)
    return bytes(result)


def zigzag(value):
    return (value << 1) ^ (value >> 31)


def field_varint(number, value):
    return varint((number << 3) | 0) + varint(value)


def field_sint(number, value):
    return field_varint(number, zigzag(value))


def field_float(number, value):
    return varint((number << 3) | 5) + struct.pack("<f", value)


def field_message(number, value):
    return varint((number << 3) | 2) + varint(len(value)) + value


def field_string(number, value):
    return field_message(number, value.encode("utf-8"))


def field_packed(number, values):
    return varint((number << 3) | 2) + varint(len(values)) + values


def summary_data(points, hrs, distance_m):
    lons = [round(lon * 3_000_000) for lon, _ in points]
    lats = [round(lat * 3_000_000) for _, lat in points]
    avg_hr = round(mean(hrs))
    avg_pace = DURATION_S / distance_m              # s/m
    calories = round(distance_m * CAL_PER_M)
    segment_distances = [haversine_m(a[0], a[1], b[0], b[1])
                         for a, b in zip(points, points[1:])]
    segment_steps = [max(0, round(distance / STRIDE_M)) for distance in segment_distances]
    steps = sum(segment_steps)
    avg_cadence = steps / DURATION_S
    max_cadence = max(segment_steps, default=0) / 60.0
    avg_stride_cm = round(distance_m * 100 / steps) if steps else 0

    record = b"".join([
        field_varint(1, TRACK_ID),
        field_varint(2, 0),                         # parentId: 0 = parent
        field_sint(3, 32),                          # UTC+8, unit: quarter-hour
        field_sint(5, lats[0]),                     # first GPS lat (*3e6)
        field_sint(6, lons[0]),                     # first GPS lng (*3e6)
        field_sint(7, 12),                          # first GPS altitude (m)
        field_varint(8, max(lats)),
        field_varint(9, min(lats)),
        field_varint(10, max(lons)),
        field_varint(11, min(lons)),
    ])
    type_msg = field_varint(1, SPORT_TYPE_OUTDOOR_HIKING) + field_varint(2, 0)
    distance_msg = field_float(1, distance_m)
    times_msg = field_varint(1, DURATION_S) + field_varint(2, DURATION_S)
    best_speed = max((distance / 60.0 for distance in segment_distances), default=0.0)
    best_pace = 1.0 / best_speed if best_speed else 0.0
    pace_msg = field_float(1, avg_pace) + field_float(2, best_pace)
    steps_msg = (
        field_float(1, avg_cadence) + field_float(2, max_cadence)
        + field_varint(3, avg_stride_cm)               # avg stride (cm)
        + field_varint(4, steps)                      # steps
    )
    calories_msg = field_varint(1, calories)
    hr_msg = field_varint(1, avg_hr) + field_varint(2, max(hrs)) + field_varint(3, min(hrs))
    chart_msg = field_varint(1, 1) + field_varint(2, 1)   # hr + speed chart
    # OtherMessage: aerobic training effect 3.0, VO2max 48
    other_msg = field_float(4, 3.0) + field_varint(7, 48)
    # HeartrateZoneMessage: reserve-heart-rate zones, 6 zones in seconds
    hr_zone_msg = (
        field_varint(1, 1)
        + field_packed(2, b"".join(varint(v) for v in (90, 105, 120, 135, 150, 165)))
        + field_packed(3, b"".join(varint(v) for v in (600, 900, 1200, 600, 300, 0)))
    )
    equip_msg = field_string(1, EQUIP_ID)
    body = b"".join([
        field_string(1, "2.1"),
        field_message(2, record),
        field_message(3, type_msg),
        field_message(4, distance_msg),
        field_message(7, times_msg),
        field_message(10, pace_msg),
        field_message(11, steps_msg),
        field_message(16, calories_msg),
        field_message(19, hr_msg),
        field_message(21, other_msg),
        field_message(22, hr_zone_msg),
        field_message(25, chart_msg),
        field_message(53, equip_msg),
    ])
    # sport_record_save.c reserves the first two bytes as 00 80 before protobuf.
    return b"\x00\x80" + body


# ---------------------------------------------------------------- detail TLV
def node_timestamp(ms):
    return struct.pack("<BBIQ", 1, 12, 0x686D7370, ms)


def node_event(relative_ms, index):
    return struct.pack("<BBHH", 4, 4, relative_ms, index)


def node_gps(relative_ms, gps_time, lon, lat, high, speed, course, hacc):
    return struct.pack(
        "<BBH I i i h H H f f", 2, 28, relative_ms, gps_time,
        round(lon * 3_000_000), round(lat * 3_000_000), high, speed, 2, course, hacc
    )


def node_hr(relative_ms, hr):
    return struct.pack("<BBHB", 8, 3, relative_ms, hr)


def node_rt_group_1(relative_ms, fre, stride, pace_s_km):
    return struct.pack("<BBHHHH", 5, 8, relative_ms, fre, stride, pace_s_km)


def node_sec_level(relative_ms, distance_cm):
    # two-byte tag 0x091F + length 6 + relative_time(2) + total_distance cm(4)
    return b"\x1f\x09" + struct.pack("<BHI", 6, relative_ms, distance_cm)


def node_lab_group(index, avg_hr, pace_s_km, cal, lap_dist_m, total_dist_m, lab_time_ms,
                   step_freq, step_stride):
    """sport_node_lab_group_t: tag 11, length 99."""
    payload = struct.pack(
        "<HHBBHHHII"      # rel_time, index, type, avg_hr, pace, cal, lab_distance,
                          # distance, lab_time
        "HHHHHHHHHH"      # strokes, swolf, avgStrokes, style, maxPace,
                          # distanceOfDownhill, skip_count, skip_fre, row_count, row_fre
        "HHBH"            # weight, times, unit, stair_floor
        "f"               # floor_per_min
        "BB"              # training_group, stress
        "HHH"             # step_freq, step_stride, avg_cadence
        "III"             # avg_power, elevation_gain, elevation_loss
        "HHHHHH"          # training_type, avg_stroke_dis, break_count, one_jump_count,
                          # glide_count, glide_height
        "ffff",           # upstair_height, avg_pull_time, avg_paddle_time, f_lap_distance
        0, index, 3, avg_hr, pace_s_km, cal, lap_dist_m, total_dist_m, lab_time_ms,
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0,          # strokes..row_fre
        0, 0, 0, 0,                            # weight, times, unit, stair_floor
        0.0,                                   # floor_per_min
        0, 0,                                  # training_group, stress
        step_freq, step_stride, 0,             # step_freq, step_stride, avg_cadence
        0, 0, 0,                               # avg_power, elevation_gain, elevation_loss
        0, 0, 0, 0, 0, 0,                     # training_type..glide_height
        0.0, 0.0, 0.0, float(lap_dist_m),      # upstair..f_lap_distance
    )
    assert len(payload) == 99, len(payload)
    return struct.pack("<BB", 11, 99) + payload


def detail_data(points, hrs, start_ms, distance_m, lap_ms_abs, lap_dists):
    """points: (lon, lat) per minute (61 pts). hrs: per second (3601)."""
    result = bytearray()
    result += node_timestamp(start_ms)
    result += node_event(0, 1)                       # SPORT_NODE_EVENT_START
    calories = round(distance_m * CAL_PER_M)
    segment_distances = [haversine_m(a[0], a[1], b[0], b[1])
                         for a, b in zip(points, points[1:])]
    segment_steps = [max(0, round(distance / STRIDE_M)) for distance in segment_distances]
    segment_strides = [round(distance * 100 / steps) if steps else round(STRIDE_M * 100)
                       for distance, steps in zip(segment_distances, segment_steps)]
    total_cm = 0
    for minute, (lon, lat) in enumerate(points):
        minute_start = minute * 60
        timestamp = start_ms + minute_start * 1000
        result += node_timestamp(timestamp)
        seg_m = segment_distances[minute] if minute < len(segment_distances) else 0.0
        result += node_gps(0, timestamp // 1000, lon, lat, 12,
                           round(seg_m), 0.0, 5.0)
        # pace of this minute (s/km) from the next point
        if minute + 1 < len(points):
            lon0, lat0 = points[minute]
            lon1, lat1 = points[minute + 1]
            seg_m = haversine_m(lon0, lat0, lon1, lat1)
            pace_s_km = int(round(60.0 * 1000 / max(seg_m, 1.0)))
        else:
            pace_s_km = 600
        step_freq = segment_steps[minute] if minute < len(segment_steps) else 0
        step_stride = segment_strides[minute] if minute < len(segment_strides) else round(STRIDE_M * 100)
        result += node_rt_group_1(0, step_freq, step_stride, pace_s_km)
        # per-second HR samples for this minute
        end_second = min(minute_start + 60, len(hrs) - 1)
        for second in range(minute_start, end_second + 1):
            if second == end_second and minute < len(points) - 1:
                break
            result += node_hr((second - minute_start) * 1000, hrs[second])
        # cumulative distance at end of this minute (cm)
        if minute + 1 < len(points):
            lon0, lat0 = points[minute]
            lon1, lat1 = points[minute + 1]
            total_cm += int(round(haversine_m(lon0, lat0, lon1, lat1) * 100))
        result += node_sec_level(0, total_cm)
        # auto lap at 30 and 60 minutes
        if minute in (30, 60):
            lap_idx = 1 if minute == 30 else 2
            lap_dist = lap_dists[lap_idx - 1]
            result += node_lab_group(
                lap_idx, round(mean(hrs[:minute * 60 + 1])),
                int(round(minute * 60.0 * 1000 / max(lap_dist, 1.0))),
                round(lap_dist * CAL_PER_M), lap_dist, int(round(lap_dist)),
                (timestamp - start_ms), step_freq, step_stride
            )
    result += node_event(0, 6)                       # SPORT_NODE_EVENT_STOP
    return bytes(result)


# ---------------------------------------------------------------- data files
def lap_data(hrs, start_ms, lap_ms_abs, lap_dists):
    """lap_detail_data_t = u64 ts + sport_node_lab_group_t(101B) + 3B pad = 112B."""
    result = bytearray()
    for index, (abs_ms, lap_dist) in enumerate(zip(lap_ms_abs, lap_dists), 1):
        record = bytearray(112)
        struct.pack_into("<Q", record, 0, start_ms + abs_ms)
        node = node_lab_group(
            index, round(mean(hrs[:index * 1800])),
            int(round(index * 1800.0 * 1000 / max(lap_dist, 1.0))),
            round(lap_dist * CAL_PER_M), lap_dist, round(lap_dist),
            index * 1800 * 1000, round(steps_for_distance(lap_dist) / 1800),
            round(STRIDE_M * 100)
        )
        record[8:8 + len(node)] = node
        result += record
    return bytes(result)


def steps_for_distance(distance_m):
    """Deterministic step estimate shared by summary and lap records."""
    return max(0, round(distance_m / STRIDE_M))


def chart_data(count, data_max, data_min, max_v, min_v, avg):
    return struct.pack("<I5f", count, data_max, data_min, max_v, min_v, avg)


def haversine_m(lon0, lat0, lon1, lat1):
    r = 6371000.0
    p0, p1 = math.radians(lat0), math.radians(lat1)
    dp = math.radians(lat1 - lat0)
    dl = math.radians(lon1 - lon0)
    a = math.sin(dp / 2) ** 2 + math.cos(p0) * math.cos(p1) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def route_distance(points):
    total = 0.0
    for (lon0, lat0), (lon1, lat1) in zip(points, points[1:]):
        total += haversine_m(lon0, lat0, lon1, lat1)
    return total


def heart_rates(hrs):
    """hr.chart + hr.data: 1200 slots, value = (hr - dataMin) * 1000 / (dataMax - dataMin)."""
    raw_max, raw_min = max(hrs), min(hrs)
    if raw_max - raw_min <= 20:
        data_max = (raw_max // 10 + 1) * 10 + 40
        data_min = max(0, raw_min // 10 * 10 - (10 if raw_min % 10 == 0 else 0))
    else:
        data_max = (raw_max // 10 + 1) * 10
        data_min = 0 if raw_min < 10 else (raw_min // 10 * 10 - (10 if raw_min % 10 == 0 else 0))
    chart = chart_data(CHART_WIDTH, float(data_max), float(data_min),
                       float(raw_max), float(raw_min), mean(hrs))
    pos = []
    for slot in range(CHART_WIDTH):
        begin = slot * len(hrs) // CHART_WIDTH
        end = max(begin + 1, (slot + 1) * len(hrs) // CHART_WIDTH)
        avg = mean(hrs[begin:end])
        pos.append(round((avg - data_min) * CHART_HEIGHT / (data_max - data_min)))
    return chart, struct.pack("<%dh" % CHART_WIDTH, *pos)


def speed_chart_data(points):
    """speed.data from per-minute pace: speed = 3600*10000/pace (dm/h)."""
    paces = []                     # s/km per minute
    for (lon0, lat0), (lon1, lat1) in zip(points, points[1:]):
        seg_m = haversine_m(lon0, lat0, lon1, lat1)
        paces.append(60.0 / max(seg_m, 1.0) * 1000.0)
    speeds = [3600.0 * 10000.0 / p for p in paces]
    raw_max, raw_min = max(speeds), min(speeds)
    max_level = (int(raw_max) // 100000 + 1) * 100000
    min_level = 0 if raw_min < 100000 else (int(raw_min) // 100000 * 100000
                                            - (100000 if int(raw_min) % 100000 == 0 else 0))
    pos = []
    for slot in range(CHART_WIDTH):
        minute = slot * DURATION_S // CHART_WIDTH // 60
        minute = min(minute, len(speeds) - 1)
        speed = speeds[minute]
        pos.append(round((speed - min_level) * CHART_HEIGHT / (max_level - min_level)))
    chart = chart_data(CHART_WIDTH, max_level / 100.0, min_level / 100.0,
                       raw_max / 100.0, raw_min / 100.0,
                       route_distance(points) / DURATION_S * 3.6)
    return chart, struct.pack("<%dh" % CHART_WIDTH, *pos)


def child_manager(hrs, distance_m):
    calories = round(distance_m * CAL_PER_M)
    result = bytearray(504)
    struct.pack_into("<IIIHH", result, 0, DURATION_S, int(distance_m), calories,
                     round(mean(hrs)), 1)
    child = struct.pack(
        "<IIIIII B3x 4I", SPORT_TYPE_OUTDOOR_HIKING, TRACK_ID, DURATION_S,
        int(distance_m), calories, 0, 0, 0, 0, 0, 0
    )
    result[64:64 + len(child)] = child
    return bytes(result)


def main():
    points = resample(ROUTE, 61)
    points[0] = (118.741050, 32.000300)          # 新城科技园A3栋
    points[-1] = (118.721361, 31.966454)         # 油坊桥
    distance_m = round(route_distance(points))
    hrs = make_heart_rates(DURATION_S + 1)
    start_ms = TRACK_ID * 1000
    lap_ms_abs = [1800 * 1000, 3600 * 1000]
    lap_dists = [round(distance_m / 2), distance_m - round(distance_m / 2)]
    OUT.mkdir(parents=True, exist_ok=True)

    (OUT / "summary.data").write_bytes(summary_data(points, hrs, distance_m))
    (OUT / "detail.data").write_bytes(detail_data(points, hrs, start_ms, distance_m,
                                                  lap_ms_abs, lap_dists))
    (OUT / "gps.data").write_bytes(b"".join(
        struct.pack("<Qii", start_ms + n * 60_000,
                    round(lon * 3_000_000), round(lat * 3_000_000))
        for n, (lon, lat) in enumerate(points)
    ))
    (OUT / "event.data").write_bytes(b"".join([
        struct.pack("<QQ", start_ms, 1),                       # START
        struct.pack("<QQ", start_ms + DURATION_S * 1000, 6),   # STOP
    ]))
    (OUT / "lap.data").write_bytes(lap_data(hrs, start_ms, lap_ms_abs, lap_dists))
    (OUT / "special.data").write_bytes(b"")
    (OUT.parent / "child.manager").write_bytes(child_manager(hrs, distance_m))

    hr_chart, hr_data = heart_rates(hrs)
    sp_chart, sp_data = speed_chart_data(points)
    (OUT / "hr.chart").write_bytes(hr_chart)
    (OUT / "hr.data").write_bytes(hr_data)
    (OUT / "speed.chart").write_bytes(sp_chart)
    (OUT / "speed.data").write_bytes(sp_data)

    print(f"wrote {OUT}")
    print(f"files={len(list(OUT.iterdir()))} points={len(points)} "
          f"hr={min(hrs)}..{max(hrs)} avg={mean(hrs):.0f} distance={distance_m:.0f}m "
          f"lap.data={len((OUT/'lap.data').read_bytes())}B "
          f"summary={len((OUT/'summary.data').read_bytes())}B "
          f"detail={len((OUT/'detail.data').read_bytes())}B")


if __name__ == "__main__":
    main()
