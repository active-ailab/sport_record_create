#!/usr/bin/env python3
"""Local web UI for generating synthetic native sport records.

Run from the repository root:
    python3 tools/sport_record_generator/web_app.py
Then open http://127.0.0.1:8765/
"""

import base64
import io
import json
import random
import struct
import zipfile
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from math import asin, atan2, cos, radians, sin, sqrt
from pathlib import Path
from statistics import mean
from xml.etree.ElementTree import Element, ElementTree, SubElement

from generate_outdoor_hike_gpx import ROUTE, resample


ROOT = Path(__file__).resolve().parents[2]
HOST = "127.0.0.1"
PORT = 8765
LOCAL_TZ = timezone(timedelta(hours=8))
SPORT_TYPES = {
    # stride is cm and is used to derive steps from the GPS distance.  A
    # bicycle has no walking cadence, so its stride is deliberately None.
    "outdoor_running": (1, "户外跑", 105, 1.05),
    "walking": (3, "健走", 70, 0.55),
    "outdoor_cycling": (4, "户外骑行", None, 0.45),
    "mountaineering": (10, "登山", 58, 0.90),
    "outdoor_hiking": (15, "户外徒步", 75, 0.70),
}
SPORT_PROFILE_BY_ID = {profile[0]: profile for profile in SPORT_TYPES.values()}

# Coordinates are used as route anchors only; the generated points are
# synthetic and must not be presented as an official trail or safety data.
MOUNTAINS = {
    "zijinshan": ("南京紫金山", 118.8506, 32.0584, 448),
    "huangshan": ("黄山", 118.1680, 30.1326, 1864),
    "taishan": ("泰山", 117.1030, 36.2540, 1545),
    "wuyi": ("武夷山", 117.7178, 27.7560, 408),
    "himalaya": ("喜马拉雅山（珠峰区域）", 86.9250, 27.9881, 8848),
}

# Publicly mapped, walkable section around the west and south sides of
# Zijinshan. Coordinates are WGS84 from OpenStreetMap ways; each way is ordered
# to form a short, repeatable loop suitable for synthetic-record previews.
ZIJINSHAN_LOOP = [
    (118.8370569, 32.0552686), (118.8390, 32.0557), (118.8414, 32.0554),
    (118.8430, 32.0562), (118.8445652, 32.0571491), (118.8459, 32.0577),
    (118.8473716, 32.0577783), (118.8484, 32.0582), (118.8497746, 32.0574139),
    (118.8512, 32.0567), (118.8519943, 32.0536517), (118.8534161, 32.0569631),
    (118.8540, 32.0580), (118.8560, 32.0618), (118.8536827, 32.0611922),
    (118.8513191, 32.0680895), (118.8480, 32.0690), (118.8450, 32.0670),
    (118.8420, 32.0640), (118.8400, 32.0600), (118.8370569, 32.0552686),
]


def varint(value):
    value = int(value)
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def zigzag(value):
    return (value << 1) ^ (value >> 31)


def fv(number, value):
    return varint(number << 3) + varint(value)


def fs(number, value):
    return fv(number, zigzag(value))


def distance_between(a, b):
    lon0, lat0 = a
    lon1, lat1 = b
    lat0r, lat1r = radians(lat0), radians(lat1)
    dlat = lat1r - lat0r
    dlon = radians(lon1 - lon0)
    h = sin(dlat / 2) ** 2 + cos(lat0r) * cos(lat1r) * sin(dlon / 2) ** 2
    return 6371000.0 * 2.0 * asin(sqrt(h))


def activity_metrics(points, sport_type="outdoor_hiking"):
    profile = SPORT_TYPES[sport_type] if isinstance(sport_type, str) else SPORT_PROFILE_BY_ID[sport_type]
    stride_cm = profile[2]
    distances = [distance_between(a, b) for a, b in zip(points, points[1:])]
    if stride_cm is None:
        strides = [0] * len(distances)
        steps = [0] * len(distances)
    else:
        strides = [max(1, round(stride_cm + 3 * sin(index / 37.0)))
                   for index in range(len(distances))]
        steps = [max(0, round(distance * 100 / stride))
                 for distance, stride in zip(distances, strides)]
    return sum(distances), distances, strides, steps


def track_metrics(points, sport_type):
    """Calculate metrics from the exact track written to the record."""
    if len(points) < 2:
        raise ValueError("轨迹至少需要两个点")
    metrics = activity_metrics(points, sport_type)
    if metrics[0] <= 0:
        raise ValueError("轨迹距离为 0，请重新选择或规划轨迹")
    return metrics


def bearing(a, b):
    lon0, lat0 = map(radians, a)
    lon1, lat1 = map(radians, b)
    return (atan2(sin(lon1 - lon0) * cos(lat1),
                  cos(lat0) * sin(lat1) - sin(lat0) * cos(lat1) * cos(lon1 - lon0))
            * 180.0 / 3.141592653589793 + 360.0) % 360.0


def ff(number, value):
    return varint((number << 3) | 5) + struct.pack("<f", float(value))


def fm(number, value):
    return varint((number << 3) | 2) + varint(len(value)) + value


def fstr(number, value):
    return fm(number, value.encode("utf-8"))


def heart_rates(count, low, high, intensity):
    rng = random.Random(20260807 + count + low * 17 + high * 31)
    value = (low + high) // 2
    result = []
    step = {"low": 2, "medium": 4, "high": 7}.get(intensity, 4)
    for index in range(count):
        value += rng.randint(-step, step)
        if index % 180 == 0:
            value += rng.choice((-step * 2, step * 2))
        value = max(low, min(high, value))
        result.append(value)
    if count > 2:
        result[count // 3] = low
        result[(count * 2) // 3] = high
    return result


def parse_time(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt.astimezone(LOCAL_TZ)


def route_points(route_name, count, start_lat, start_lon, end_lat, end_lon):
    if route_name == "amap":
        raise ValueError("高德路线为空，请先点击“规划高德路线”")
    if route_name == "a3_youfangqiao":
        # Keep the road-shaped route, but anchor it to the coordinates entered
        # by the user.  The old implementation silently returned the built-in
        # Nanjing coordinates here, which made both gps.data and detail.data
        # disagree with the form values.
        base = resample(ROUTE, count)
        base_start, base_end = base[0], base[-1]
        lon_span = base_end[0] - base_start[0]
        lat_span = base_end[1] - base_start[1]
        if abs(lon_span) < 1e-12 or abs(lat_span) < 1e-12:
            raise ValueError("内置轨迹端点无效")
        return [
            (start_lon + (lon - base_start[0]) / lon_span * (end_lon - start_lon),
             start_lat + (lat - base_start[1]) / lat_span * (end_lat - start_lat))
            for lon, lat in base
        ]
    if route_name == "straight":
        return [
            (start_lon + (end_lon - start_lon) * n / (count - 1),
             start_lat + (end_lat - start_lat) * n / (count - 1))
            for n in range(count)
        ]
    raise ValueError("不支持的轨迹类型")


def mountain_points(mountain_id, count):
    """Return a resampled, mapped Zijinshan loop for a synthetic record."""
    if mountain_id != "zijinshan":
        raise ValueError("当前公开步道数据仅覆盖南京紫金山，请选择紫金山或导入高德路线")
    return resample(ZIJINSHAN_LOOP, count)


def summary_data(track_id, sport_type, points, hrs, duration, distance, calories, has_gps=True):
    measured_distance, speeds, strides, step_counts = track_metrics(points, sport_type)
    if has_gps:
        distance = measured_distance
    else:
        speed = distance / duration if duration else 0.0
        speeds = [speed] * max(1, len(points) - 1)
        stride_cm = SPORT_TYPES[next(key for key, profile in SPORT_TYPES.items() if profile[0] == sport_type)][2]
        steps = round(distance * 100 / stride_cm) if stride_cm else 0
        step_counts = [steps]
        strides = [stride_cm or 0]
    lons = [round(lon * 3_000_000) for lon, _ in points]
    lats = [round(lat * 3_000_000) for _, lat in points]
    record = b"".join([
        fv(1, track_id), fv(2, 0), fv(3, 32), fv(5, lats[0]), fv(6, lons[0]),
        fv(7, 12), fv(8, max(lats)), fv(9, min(lats)),
        fv(10, max(lons)), fv(11, min(lons)),
    ])
    type_msg = fv(1, sport_type) + fv(2, 0)
    times_msg = fv(1, duration) + fv(2, duration)
    speed = distance / duration if duration else 0
    pace_msg = ff(1, 1 / speed if speed else 0) + ff(2, 1 / max(speeds) if speeds else 0)
    total_steps = sum(step_counts)
    steps_msg = None
    if strides and any(step_counts):
        steps_msg = (ff(1, total_steps / duration if duration else 0) +
                     ff(2, max(step_counts) if step_counts else 0) +
                     fv(3, round(distance * 100 / total_steps) if total_steps else 0) +
                     fv(4, total_steps))
    distance_msg = ff(1, distance)
    hr_msg = fv(1, round(mean(hrs))) + fv(2, max(hrs)) + fv(3, min(hrs))
    body = [
        fstr(1, "2.1"), fm(2, record), fm(3, type_msg), fm(4, distance_msg),
        fm(7, times_msg), fm(10, pace_msg),
        fm(16, fv(1, calories)), fm(19, hr_msg),
    ]
    if steps_msg is not None:
        body.insert(6, fm(11, steps_msg))
    return b"\x00\x80" + b"".join(body)


def detail_data(points, hrs, start_ms, sport_type, has_gps=True):
    _, speeds, strides, step_counts = track_metrics(points, sport_type)
    result = bytearray(struct.pack("<BBIQ", 1, 12, 0x686D7370, start_ms))
    result += struct.pack("<BBHH", 4, 4, 0, 1)
    previous_gps = None
    detail_epoch = start_ms
    for second, (lon, lat) in enumerate(points):
        timestamp = start_ms + second * 1000
        # Firmware keeps one absolute timestamp and stores subsequent node
        # times as uint16 relative milliseconds.  It emits another timestamp
        # before the 0x8000 ms limit, rather than putting a timestamp before
        # every GPS point.
        relative_ms = timestamp - detail_epoch
        if relative_ms > 30_000:
            result += struct.pack("<BBIQ", 1, 12, 0x686D7370, timestamp)
            detail_epoch = timestamp
            relative_ms = 0
        if has_gps:
            lng = round(lon * 3_000_000)
            lat = round(lat * 3_000_000)
            gps_speed_m_min = round(speeds[second - 1] * 60) if second else 0
            course = bearing(points[second], points[min(second + 1, len(points) - 1)])
            # Firmware GPS nodes are omitted entirely for band records.
            use_full = (previous_gps is None or second % 120 == 0 or
                        abs(lng - previous_gps[0]) > 32767 or
                        abs(lat - previous_gps[1]) > 32767)
            if use_full:
                result += struct.pack(
                    "<BBH I i i h H H f f", 2, 28, relative_ms,
                    timestamp // 1000, lng, lat, 12, gps_speed_m_min, 1,
                    course, 3.5
                )
            else:
                result += struct.pack(
                    "<BBHhhHff", 3, 16, relative_ms,
                    lng - previous_gps[0], lat - previous_gps[1], 1,
                    course, 3.5
                )
            previous_gps = (lng, lat)
        pace = round(1000 / speeds[second - 1]) if second and speeds[second - 1] else 0
        if strides and any(step_counts):
            result += struct.pack("<BBHHHH", 5, 8, relative_ms,
                                  step_counts[second - 1] * 60 if second else 0,
                                  strides[second - 1] if second else strides[0], pace)
        result += struct.pack("<BBHB", 8, 3, relative_ms, hrs[second])
    result += struct.pack("<BBHH", 4, 4, 0, 6)
    return bytes(result)


def make_gpx(points, hrs, start):
    root = Element("gpx", {
        "version": "1.1", "creator": "sport record generator web UI",
        "xmlns": "http://www.topografix.com/GPX/1/1",
        "xmlns:gpxtpx": "http://www.garmin.com/xmlschemas/TrackPointExtension/v1",
    })
    metadata = SubElement(root, "metadata")
    SubElement(metadata, "name").text = "Generated sport record"
    SubElement(metadata, "time").text = start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    track = SubElement(root, "trk")
    SubElement(track, "name").text = "Generated sport record"
    segment = SubElement(track, "trkseg")
    for index, ((lon, lat), hr) in enumerate(zip(points, hrs)):
        point = SubElement(segment, "trkpt", {"lat": f"{lat:.6f}", "lon": f"{lon:.6f}"})
        SubElement(point, "ele").text = "12"
        SubElement(point, "time").text = (start + timedelta(seconds=index)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        ext = SubElement(SubElement(point, "extensions"), "gpxtpx:TrackPointExtension")
        SubElement(ext, "gpxtpx:hr").text = str(hr)
    stream = io.BytesIO()
    ElementTree(root).write(stream, encoding="utf-8", xml_declaration=True)
    return stream.getvalue()


def make_record_files(track_id, sport_type, points, hrs, duration, distance, calories, start, has_gps=True):
    start_ms = track_id * 1000
    distance, speeds, _, _ = track_metrics(points, sport_type)
    if not has_gps:
        speeds = [distance / duration if duration else 0.0] * max(1, len(points) - 1)
    files = {
        "summary.data": summary_data(track_id, sport_type, points, hrs, duration, distance, calories, has_gps),
        "detail.data": detail_data(points, hrs, start_ms, sport_type, has_gps),
        "event.data": struct.pack("<QQQQ", start_ms, 1, start_ms + duration * 1000, 6),
        "special.data": b"",
        "lap.data": b"\x00" * 336,
    }
    if has_gps:
        files["gps.data"] = b"".join(struct.pack("<Qii", start_ms + n * 1_000, round(lon * 3_000_000), round(lat * 3_000_000)) for n, (lon, lat) in enumerate(points))
    else:
        files.pop("pace.whole", None)
    chart_hrs = []
    for slot in range(1200):
        begin = slot * len(hrs) // 1200
        end = max(begin + 1, (slot + 1) * len(hrs) // 1200)
        chart_hrs.append(mean(hrs[begin:end]))
    files["hr.chart"] = struct.pack("<I5f", 1200, 160.0, 80.0, max(hrs), min(hrs), mean(hrs))
    files["hr.data"] = struct.pack("<1200h", *[round((x - 80) * 1000 / 80) for x in chart_hrs])
    raw_max, raw_min = max(speeds), min(speeds)
    chart_max = max(1.0, (int(raw_max * 10) + 9) // 10 / 10)
    files["speed.chart"] = struct.pack("<I5f", 1200, chart_max, 0.0, raw_max, raw_min, mean(speeds))
    files["speed.data"] = struct.pack("<1200h", *[
        round(speeds[min(i * len(speeds) // 1200, len(speeds) - 1)] * 1000 / chart_max)
        for i in range(1200)])
    files["pace.whole"] = b"".join(struct.pack("<QH", start_ms + i * 1_000, round(1000 / speeds[i]) if speeds[i] else 0) for i in range(len(speeds)))
    return files


def manager_bytes(existing, track_id, sport_type, duration, distance):
    data = bytearray(existing if existing else b"\x00" * 2004)
    if len(data) != 2004:
        raise ValueError("rec.manager 必须是 2004 字节")
    count, _ = struct.unpack_from("<HH", data, 0)
    if count >= 100:
        raise ValueError("rec.manager 已满")
    entries = [bytes(data[4 + i * 20:4 + (i + 1) * 20]) for i in range(count)]
    entries = [entry for entry in entries if struct.unpack_from("<I", entry, 12)[0] != track_id]
    record = struct.pack("<BbHI f I BBBB", 0x11, 32, sport_type, duration, float(distance), track_id, 0, 0, 0, 0)
    entries.append(record)
    data[4:] = b"\0" * (len(data) - 4)
    for i, entry in enumerate(entries):
        data[4 + i * 20:4 + (i + 1) * 20] = entry
    struct.pack_into("<H", data, 0, len(entries))
    return bytes(data)


def total_bytes(existing, duration, distance, calories):
    data = bytearray(existing if existing else b"\x00" * 64)
    if len(data) != 64:
        raise ValueError("total.info 必须是 64 字节")
    old = struct.unpack_from("<IIII", data, 0)
    struct.pack_into("<IIII", data, 0, old[0] + int(distance), old[1] + calories, old[2] + 1, old[3] + duration)
    return bytes(data)


def child_manager(track_id, sport_type, duration, distance, calories, hrs):
    result = bytearray(504)
    struct.pack_into("<IIIHH", result, 0, duration, int(distance), calories, round(mean(hrs)), 1)
    child = struct.pack("<IIIIII B3x 4I", sport_type, track_id, duration, int(distance), calories, 0, 0, 0, 0, 0, 0)
    result[64:64 + len(child)] = child
    return bytes(result)


def generate(payload):
    sport_type, sport_name, _, calorie_factor = SPORT_TYPES[payload["sport_type"]]
    start = parse_time(payload["start"])
    end = parse_time(payload["end"])
    duration = int((end - start).total_seconds())
    if duration < 1 or duration > 24 * 3600:
        raise ValueError("运动时长必须在 1 秒到 24 小时之间")
    low, high = int(payload["hr_low"]), int(payload["hr_high"])
    if not (0 <= low < high <= 220):
        raise ValueError("心率区间不合法")
    project = payload.get("project", "watch")
    if project not in ("watch", "band"):
        raise ValueError("请选择手表或手环项目")
    has_gps = project == "watch"
    track_id = int(start.timestamp())
    sample_count = duration + 1
    start_lat, start_lon = float(payload["start_lat"]), float(payload["start_lon"])
    end_lat, end_lon = float(payload["end_lat"]), float(payload["end_lon"])
    # Keep the mountain picker authoritative even if an older browser keeps
    # the previous route select value after changing the sport type.
    route_name = payload.get("route")
    if payload["sport_type"] == "mountaineering" and route_name != "amap":
        route_name = "mountain"
    if not has_gps:
        points = [(0.0, 0.0), (0.001, 0.001)]
    elif route_name == "mountain":
        points = mountain_points(payload.get("mountain", ""), sample_count)
    elif route_name == "amap":
        raw = payload.get("amap_points") or []
        if len(raw) < 2:
            raise ValueError("高德路线为空，请先点击“规划高德路线”")
        points = [(float(p[0]), float(p[1])) for p in raw]
        # AMap returns a route at its own sampling density; normalize it to
        # one point per generated second for the native record format.
        points = resample(points, sample_count)
    else:
        points = route_points(payload["route"], sample_count, start_lat, start_lon, end_lat, end_lon)
    hrs = heart_rates(sample_count, low, high, payload["intensity"])
    if not has_gps:
        # Band records contain no location data. Model distance from the
        # activity profile and duration instead of synthesizing coordinates.
        distance = duration * (1.2 if payload["sport_type"] == "outdoor_cycling" else 0.7)
        stride_m = {"outdoor_running": 1.05, "walking": 0.70,
                    "outdoor_cycling": 1.0, "mountaineering": 0.58,
                    "outdoor_hiking": 0.75}[payload["sport_type"]]
        steps = [round(distance / stride_m)]
        points = [(0.0, 0.0), (distance / 111_320, 0.000001)]
    else:
        distance, _, _, steps = track_metrics(points, sport_type)
    total_steps = sum(steps)
    calories = int(payload.get("calories") or max(1, distance * calorie_factor / 10))
    native = make_record_files(track_id, sport_type, points, hrs, duration, distance, calories, start, has_gps)
    manager_input = base64.b64decode(payload["manager_data"]) if payload.get("manager_data") else None
    total_input = base64.b64decode(payload["total_data"]) if payload.get("total_data") else None
    manager = None
    total = None
    if payload["manager_action"] in ("create", "update"):
        manager = manager_bytes(manager_input if payload["manager_action"] == "update" else None, track_id, sport_type, duration, distance)
        total = total_bytes(total_input, duration, distance, calories)

    zip_stream = io.BytesIO()
    with zipfile.ZipFile(zip_stream, "w", zipfile.ZIP_DEFLATED) as archive:
        base = f"{track_id}/{track_id}/"
        for name, content in native.items():
            archive.writestr(base + name, content)
        archive.writestr(f"{track_id}/child.manager", child_manager(track_id, sport_type, duration, distance, calories, hrs))
        if has_gps:
            archive.writestr(f"{track_id}.gpx", make_gpx(points, hrs, start))
        if manager is not None:
            archive.writestr("rec.manager", manager)
            archive.writestr("total.info", total)
        archive.writestr("record.json", json.dumps({"track_id": track_id, "project": project, "has_gps": has_gps, "sport": sport_name, "start": start.isoformat(), "end": end.isoformat(), "duration": duration, "distance": round(distance), "steps": total_steps, "hr_min": min(hrs), "hr_max": max(hrs)}, ensure_ascii=False, indent=2))
    return track_id, zip_stream.getvalue()


HTML = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>运动记录生成器</title>
<style>
:root{font:15px/1.5 Inter,"PingFang SC","Microsoft YaHei",sans-serif;color:#e6edf3;background:#0c131b;--ink:#e6edf3;--muted:#91a0ad;--line:#263442;--accent:#c9f04f;--green:#c9f04f;--paper:#141e29}*{box-sizing:border-box}body{margin:0}.shell{max-width:1320px;margin:auto;padding:30px 34px 60px}.hero{display:flex;align-items:center;justify-content:space-between;margin:0 0 20px;padding:0 2px}.eyebrow{font-size:11px;letter-spacing:.17em;color:#91a0ad;font-weight:700}.hero h1{font-size:30px;letter-spacing:-.035em;margin:5px 0 2px;color:#f3f7fa}.hero p{margin:0;color:#95a5b1}.pill{font-size:12px;color:#aebcc7;background:#16232f;border:1px solid #283744;padding:8px 12px;border-radius:6px}.workspace{display:grid;grid-template-columns:minmax(0,1fr) 330px;gap:42px;align-items:start}.main-column,.side-column{display:grid;gap:30px}.side-column{position:sticky;top:24px}.panel{background:transparent;border:0;border-radius:0;padding:0;margin:0;box-shadow:none;border-bottom:1px solid var(--line);padding-bottom:25px}.side-column .panel{background:#141f2a;border:1px solid #293947;padding:20px;border-radius:10px}.panel h2{font-size:16px;letter-spacing:-.01em;font-weight:700;margin:0 0 15px;display:flex;align-items:center;gap:9px}.step{font-size:11px;color:#b9d947;background:#22303b;padding:3px 6px;border-radius:4px}.device-panel{position:relative;margin-bottom:30px;padding:20px 22px 18px;border:1px solid #283c4c;border-radius:12px;background:linear-gradient(112deg,#101c29,#172b3b);color:#eef6fc;box-shadow:0 12px 30px #0c1b2b24}.device-heading{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}.device-heading .eyebrow{color:#8298aa}.device-heading h2{font-size:17px;margin:3px 0 0;letter-spacing:.01em}.device-heading p{font-size:12px;color:#91a5b5;margin:0}.device-live{font:11px ui-monospace,monospace;letter-spacing:.12em;color:#a1b0bb}.device-live i{display:inline-block;width:7px;height:7px;border-radius:50%;background:#c4ef56;box-shadow:0 0 10px #c4ef56;margin-right:7px}.device-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.device{display:flex;align-items:center;gap:12px;padding:13px 15px;border:1px solid #344a5a;border-radius:8px;cursor:pointer;background:#1a2c3b;color:#dce7ef;transition:.16s ease}.device:has(input:checked){border-color:#c9f04f;background:#243348;color:#fff;box-shadow:inset 3px 0 #c9f04f,0 0 20px #c9f04f12}.device input{width:auto;min-width:0;accent-color:#819523}.device strong{display:block;font-weight:700}.device small{font-size:12px;color:#9bb0bf}.subtle{font-size:12px;color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:17px 20px}.field{min-width:0}label{display:block;font-size:12px;font-weight:650;color:#aebbc5;margin-bottom:6px}input,select{font:inherit;font-size:14px;color:#e4edf3;width:100%;min-width:0;border:1px solid #2c3a47;background:#131e28;border-radius:7px;padding:10px 11px;outline:none}input:focus,select:focus{border-color:#c9f04f;box-shadow:0 0 0 2px #c9f04f22}.inline{display:flex;gap:8px;align-items:center}.inline input{min-width:0}.gps-fields.hidden{display:none}.gps-fields .grid[style]{margin-top:17px!important}#map{height:280px;border:1px solid var(--line);border-radius:8px;margin-top:14px;overflow:hidden;background:#152431}.saved-state{float:right;color:#9aa8b3;font-weight:400}.saved-state.saved{color:#c9f04f}.map-note{font-size:11px;line-height:1.55;color:var(--muted);margin:8px 0 0}.btn{border:0;border-radius:7px;padding:11px 15px;background:#c9f04f;color:#101812;font:600 13px inherit;cursor:pointer}.btn:hover{background:#d9ff70}.btn.secondary{background:#253442;color:#d9e5ed;white-space:nowrap}.index-help{margin:0 0 13px;color:var(--muted);font-size:12px}.index-options{display:grid;gap:5px;margin:12px 0}.index-choice{display:flex;align-items:center;gap:9px;padding:8px 9px;border-radius:6px;background:#101923;color:#c8d4dd;font-size:13px;cursor:pointer}.index-choice input{width:auto;min-width:0;accent-color:#819523}.index-choice:has(input:checked){background:#27351e;color:#eff8df}.index-upload{border-top:1px solid var(--line);padding-top:13px;margin-top:12px}.index-upload[hidden]{display:none}.index-files{display:grid;gap:12px}.index-actions{margin-top:13px}.result-panel{background:#14212b!important;border:1px solid #2a3b48!important;border-radius:10px!important;padding:18px!important}.result-panel h2{margin-bottom:8px}.panel-note{font-size:12px;color:#9cabb5;margin:0 0 14px}.result-panel .btn{background:#c9f04f;color:#101812;width:100%;padding:13px}.result-panel .btn:hover{background:#d9ff70}#status{white-space:pre-wrap;color:#a7b58b;margin-top:12px;font-size:12px;min-height:18px}#planAmap{white-space:nowrap}@media(max-width:860px){.workspace{grid-template-columns:1fr;gap:30px}.side-column{position:static;grid-template-columns:1fr 1fr}.side-column>.panel:last-child{grid-column:1/-1}}@media(max-width:620px){.shell{padding:22px 17px 40px}.device-panel{padding:16px}.device-grid{grid-template-columns:1fr}.hero{align-items:flex-start;gap:10px}.hero h1{font-size:25px}.pill{display:none}.workspace{gap:24px}.main-column,.side-column{gap:22px}.side-column,.device-grid,.grid{grid-template-columns:1fr}.device-grid{display:grid}.side-column>.panel:last-child{grid-column:auto}.panel{padding-bottom:20px}.side-column .panel{padding:16px}.inline{gap:6px}.gps-fields .grid[style]{margin-top:13px!important}}
</style></head><body><main class="shell"><header class="hero"><div><div class="eyebrow">SPORT RECORD LAB</div><h1>运动记录生成器</h1><p>配置设备与运动参数，生成可导入的记录 ZIP。</p></div><span class="pill">本地生成 · 不覆盖设备文件</span></header>
<form id="form"><section class="device-panel"><div class="device-heading"><div><span class="eyebrow">TARGET DEVICE</span><h2>选择设备项目</h2><p>设备能力会自动匹配记录内容</p></div><span class="device-live"><i></i> READY</span></div><div class="device-grid"><label class="device"><input type="radio" name="project" value="watch" checked><span><strong>手表项目</strong><small>包含 GPS 轨迹、地图预览和 GPX</small></span></label><label class="device"><input type="radio" name="project" value="band"><span><strong>手环项目</strong><small>不包含 GPS、坐标、地图或 GPX</small></span></label></div></section><div class="workspace"><div class="main-column">
<section class="panel"><h2><span class="step">01</span>运动参数</h2><div class="grid"><div class="field"><label>运动类型</label><select name="sport_type"><option value="outdoor_hiking">户外徒步</option><option value="walking">健走</option><option value="outdoor_running">户外跑</option><option value="outdoor_cycling">户外骑行</option><option value="mountaineering">登山</option></select></div><div class="field"><label>运动强度</label><select name="intensity"><option value="low">低</option><option value="medium" selected>中</option><option value="high">高</option></select></div><div class="field"><label>开始时间</label><input name="start" type="datetime-local"></div><div class="field"><label>结束时间</label><input name="end" type="datetime-local"></div><div class="field"><label>心率范围（下限 / 上限）</label><div class="inline"><input name="hr_low" type="number" value="90" min="0" max="220"><input name="hr_high" type="number" value="150" min="1" max="220"></div></div><div class="field"><label>热量（kcal，可选）</label><input name="calories" type="number" value="430" min="0"></div></div></section>
<section class="panel gps-fields" id="gpsPanel"><h2><span class="step">03</span>GPS 路线</h2><div class="grid"><div class="field"><label>路线来源</label><select name="route"><option value="mountain">推荐：紫金山公开地图步道</option><option value="a3_youfangqiao">内置道路形状</option><option value="straight">起点到终点直线</option><option value="amap">高德规划路线</option></select></div><div class="field"><label>建议路线</label><input value="南京紫金山 · 公开地图步道环线建议" readonly></div></div><div class="grid" style="margin-top:14px"><div class="field"><label>起点纬度 / 经度</label><div class="inline"><input name="start_lat" value="32.055269"><input name="start_lon" value="118.837057"></div></div><div class="field"><label>终点纬度 / 经度</label><div class="inline"><input name="end_lat" value="32.055269"><input name="end_lon" value="118.837057"></div></div></div><div class="grid" style="margin-top:14px"><div class="field"><label>高德 JS API Key</label><div class="inline"><input id="amapKey" placeholder="输入高德 JS API Key"><button type="button" class="btn secondary" id="saveAmap">保存</button></div></div><div class="field"><label>高德安全码 <span id="amapSaved" class="saved-state">未保存</span></label><div class="inline"><input id="amapSecurity" placeholder="可选"><button type="button" class="btn secondary" id="planAmap">规划路线</button></div></div></div><div id="map"></div><p class="map-note">推荐线来自 OpenStreetMap 已映射步道；这是记录生成用路线，出行前请核对景区开放与现场路况。地图数据 © OpenStreetMap contributors。</p></section></div><div class="side-column">
<section class="panel"><h2><span class="step">04</span>索引文件 <span class="subtle" style="font-size:13px;font-weight:400">（可选）</span></h2><p class="index-help">索引文件用于让新记录出现在设备记录列表中。只生成运动记录 ZIP 时可保持关闭。</p><div class="index-options"><label class="index-choice"><input type="radio" name="index_mode" value="none" checked>不生成索引</label><label class="index-choice"><input type="radio" name="index_mode" value="create">新建索引文件</label><label class="index-choice"><input type="radio" name="index_mode" value="update">追加到现有索引</label></div><div class="index-upload" id="indexUpload" hidden><div id="createHelp" class="index-help" hidden>将创建新的 rec.manager 和 total.info。</div><div id="updateHelp" class="index-help" hidden>选择设备中的 rec.manager；total.info 可选，用于累计汇总。</div><div class="index-files"><div class="field" id="managerField"><label for="managerFile">rec.manager <span class="subtle">必需</span></label><input id="managerFile" type="file" accept=".manager,.bin"></div><div class="field" id="totalField"><label for="totalFile">total.info <span class="subtle">可选</span></label><input id="totalFile" type="file" accept=".info,.bin"></div></div></div><div class="index-actions"><span class="subtle">ZIP 内生成，不修改原文件</span></div><select name="manager_action" hidden><option value="none">不生成</option><option value="create">创建新的</option><option value="update">追加现有</option></select></section><section class="panel result-panel"><h2><span class="step">05</span>生成记录</h2><p class="panel-note">确认设备和参数后生成 ZIP，可导入设备验证。</p><button class="btn" style="width:100%;padding:13px" type="submit">生成并下载 ZIP</button><div id="status" aria-live="polite">准备就绪</div></section></div></div></form></main>
<script>const f=document.querySelector('#form'),s=document.querySelector('#status');let amapPoints=null,amapMap=null,amapLine=null;
function localDateTime(d){let p=n=>String(n).padStart(2,'0');return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate())+'T'+p(d.getHours())+':'+p(d.getMinutes())}let now=new Date();f.start.value=localDateTime(now);f.end.value=localDateTime(new Date(now.getTime()+3600000));
const mountainCoords={zijinshan:[32.0520,118.8480,32.0637,118.8560]};
const recommendedTrail=[[118.8370569,32.0552686],[118.839,32.0557],[118.8414,32.0554],[118.843,32.0562],[118.8445652,32.0571491],[118.8459,32.0577],[118.8473716,32.0577783],[118.8484,32.0582],[118.8497746,32.0574139],[118.8512,32.0567],[118.8519943,32.0536517],[118.8534161,32.0569631],[118.854,32.058],[118.856,32.0618],[118.8536827,32.0611922],[118.8513191,32.0680895],[118.848,32.069],[118.845,32.067],[118.842,32.064],[118.84,32.06],[118.8370569,32.0552686]];
function drawMountainPreview(){let c=recommendedTrail,svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','118.832 32.045 0.03 0.03');svg.setAttribute('preserveAspectRatio','xMidYMid meet');svg.style.cssText='width:100%;height:100%;background:#eaf2eb';let bg=document.createElementNS(svg.namespaceURI,'path');bg.setAttribute('d','M118.832 32.045h.03v.03h-.03z');bg.setAttribute('fill','#eaf2eb');svg.appendChild(bg);let line=document.createElementNS(svg.namespaceURI,'polyline');line.setAttribute('points',c.map(p=>`${p[0]},${p[1]}`).join(' '));line.setAttribute('fill','none');line.setAttribute('stroke','#16815b');line.setAttribute('stroke-width','.00045');line.setAttribute('stroke-linecap','round');line.setAttribute('stroke-linejoin','round');svg.appendChild(line);document.querySelector('#map').replaceChildren(svg);f.start_lat.value=c[0][1].toFixed(6);f.start_lon.value=c[0][0].toFixed(6);f.end_lat.value=c.at(-1)[1].toFixed(6);f.end_lon.value=c.at(-1)[0].toFixed(6);f.route.value='mountain';amapPoints=c;s.textContent='推荐路线：紫金山公开地图步道环线建议。轨迹基于 OpenStreetMap 数据，仅用于测试记录。'}
function setMountain(){f.mountain.value='zijinshan';f.sport_type.value='mountaineering';amapPoints=null;drawMountainPreview()}
f.mountain.onchange=setMountain;f.sport_type.onchange=()=>{if(f.sport_type.value==='mountaineering')setMountain()};
document.querySelectorAll('[name=project]').forEach(el=>el.onchange=()=>{let gps=f.project.value==='watch';document.querySelector('#gpsPanel').classList.toggle('hidden',!gps);if(!gps)s.textContent='手环项目：将生成无 GPS 的记录包。'});
const indexUpload=document.querySelector('#indexUpload');document.querySelectorAll('[name=index_mode]').forEach(el=>el.onchange=()=>{let mode=f.index_mode.value;f.manager_action.value=mode;indexUpload.hidden=mode==='none';document.querySelector('#createHelp').hidden=mode!=='create';document.querySelector('#updateHelp').hidden=mode!=='update';document.querySelector('#managerField').hidden=mode!=='update';document.querySelector('#totalField').hidden=mode==='none'});
f.route.onchange=()=>{if(f.route.value==='mountain')drawMountainPreview()};f.mountain.onchange=()=>{if(f.mountain.value!=='zijinshan'){f.route.value='amap';s.textContent='当前公开步道建议仅覆盖南京紫金山，请为此地点规划高德路线。';return}drawMountainPreview()};
drawMountainPreview();
const savedAmapKey=localStorage.getItem('sport-record-amap-key')||'',savedAmapSecurity=localStorage.getItem('sport-record-amap-security')||'';amapKey.value=savedAmapKey;amapSecurity.value=savedAmapSecurity;const amapSaved=document.querySelector('#amapSaved');function showAmapSaved(saved){amapSaved.textContent=saved?'已保存到此浏览器':'未保存';amapSaved.classList.toggle('saved',saved)}showAmapSaved(!!(savedAmapKey||savedAmapSecurity));document.querySelector('#saveAmap').onclick=()=>{localStorage.setItem('sport-record-amap-key',amapKey.value.trim());localStorage.setItem('sport-record-amap-security',amapSecurity.value.trim());showAmapSaved(true)};
function b64(file){return new Promise((ok,fail)=>{if(!file)return ok('');let r=new FileReader();r.onload=()=>ok(r.result.split(',')[1]);r.onerror=fail;r.readAsDataURL(file)})}
function planAmap(){let key=document.querySelector('#amapKey').value.trim(),security=document.querySelector('#amapSecurity').value.trim(),slat=+f.start_lat.value,slon=+f.start_lon.value,elat=+f.end_lat.value,elon=+f.end_lon.value;if(!key){s.textContent='请先输入高德 JS API Key';return}const ready=()=>{let cycling=f.sport_type.value==='outdoor_cycling',service=cycling?new AMap.Riding({policy:0}):new AMap.Walking({policy:0});amapMap=amapMap||new AMap.Map('map',{zoom:13,center:[slon,slat]});s.textContent='正在请求高德真实路线…';service.search([slon,slat],[elon,elat],(status,result)=>{if(status!=='complete'){if(f.sport_type.value==='mountaineering'){f.route.value='mountain';amapPoints=null;drawMountainPreview();s.textContent='高德没有该山的步道数据，已使用内置登山路线。';return}s.textContent='高德没有找到该山的可步行路线：'+(result||'请更换登山入口或检查 Key');return}let r=result.routes&&result.routes[0],steps=cycling?(r&&r.rides||[]):(r&&r.steps||[]),path=steps.flatMap(x=>(x.path||[]).map(p=>[Number(p.lng??p[0]),Number(p.lat??p[1])]));if(path.length<2){if(f.sport_type.value==='mountaineering'){f.route.value='mountain';amapPoints=null;drawMountainPreview();s.textContent='高德没有该山的步道数据，已使用内置登山路线。';return}s.textContent='高德没有返回可用路线点，请更换登山入口';return}amapPoints=f.sport_type.value==='mountaineering'?path.concat(path.slice(0,-1).reverse()):path;f.route.value='amap';if(amapLine)amapMap.remove(amapLine);amapLine=new AMap.Polyline({path:amapPoints,strokeColor:'#1677ff',strokeWeight:6});amapMap.add(amapLine);amapMap.setFitView([amapLine]);s.textContent='高德真实路线已加载（登山往返），生成时将按每秒采样写入轨迹。'})};if(window.AMap){ready();return}if(security)window._AMapSecurityConfig={securityJsCode:security};let script=document.createElement('script');script.src='https://webapi.amap.com/maps?v=2.0&key='+encodeURIComponent(key)+'&plugin=AMap.Walking,AMap.Riding';script.onload=ready;script.onerror=()=>s.textContent='高德地图脚本加载失败，请检查网络和 Key';document.head.appendChild(script)}
document.querySelector('#planAmap').onclick=planAmap;
f.onsubmit=async e=>{e.preventDefault();s.textContent='正在生成…';let p=Object.fromEntries(new FormData(f));p.amap_points=amapPoints;p.manager_data=await b64(document.querySelector('#managerFile').files[0]);p.total_data=await b64(document.querySelector('#totalFile').files[0]);try{let r=await fetch('/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});if(!r.ok)throw new Error(await r.text());let blob=await r.blob(),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=r.headers.get('X-Record-Name')||'sport_record.zip';a.click();s.textContent=`生成成功 · ${p.project==='watch'?'手表 / GPS':'手环 / 无 GPS'}\n文件大小：${Math.round(blob.size/1024)} KB`; }catch(x){s.textContent='生成失败：'+x.message}};</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/":
            self.send_error(404)
            return
        body = HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/generate":
            self.send_error(404)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(size))
            track_id, content = generate(payload)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f"attachment; filename=sport_record_{track_id}.zip")
            self.send_header("X-Record-Name", f"sport_record_{track_id}.zip")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as exc:
            body = str(exc).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


if __name__ == "__main__":
    print(f"Sport record generator: http://{HOST}:{PORT}/")
    HTTPServer((HOST, PORT), Handler).serve_forever()
