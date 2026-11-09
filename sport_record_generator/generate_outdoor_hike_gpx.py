#!/usr/bin/env python3
"""Generate a GPX hiking activity with GPS and heart-rate samples."""

from datetime import datetime, timedelta, timezone
from math import hypot
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, ElementTree
import random


OUT = Path("artifacts/outdoor_hike_20260807_1500_1600.gpx")
START = datetime(2026, 8, 7, 15, 0, tzinfo=timezone(timedelta(hours=8)))

# Road-following geometry from the A3 building area to Youfangqiao.
# Coordinates are longitude, latitude, sampled from a public road route.
ROUTE = [
    (118.740649, 32.000513), (118.740481, 32.000286),
    (118.740369, 32.000124), (118.738892, 31.998438),
    (118.736352, 31.995624), (118.736205, 31.995474),
    (118.736348, 31.995374), (118.737895, 31.994356),
    (118.738124, 31.994195), (118.740769, 31.992429),
    (118.740904, 31.992339), (118.742166, 31.991657),
    (118.742102, 31.991564), (118.741036, 31.990146),
    (118.740480, 31.989417), (118.740320, 31.989096),
    (118.739702, 31.988378), (118.739113, 31.987620),
    (118.738895, 31.987267), (118.738212, 31.986434),
    (118.737591, 31.985685), (118.735778, 31.983625),
    (118.735212, 31.982865), (118.734782, 31.982299),
    (118.734487, 31.981977), (118.734172, 31.981576),
    (118.733593, 31.981040), (118.732208, 31.979992),
    (118.731951, 31.979841), (118.731436, 31.979486),
    (118.731232, 31.979318), (118.731053, 31.979154),
    (118.731008, 31.979101), (118.730972, 31.979052),
    (118.730936, 31.978943), (118.730942, 31.978874),
    (118.730979, 31.978762), (118.730942, 31.978721),
    (118.730877, 31.978659), (118.730744, 31.978529),
    (118.730181, 31.978067), (118.725888, 31.974776),
    (118.725730, 31.974656), (118.725796, 31.974595),
    (118.726091, 31.974301), (118.726123, 31.974268),
    (118.726280, 31.974112), (118.724490, 31.972679),
    (118.725015, 31.972178), (118.725525, 31.971878),
    (118.724340, 31.969983), (118.723366, 31.968383),
    (118.723253, 31.968198), (118.723087, 31.967912),
    (118.722837, 31.967901), (118.722553, 31.968361),
    (118.722043, 31.968834), (118.721850, 31.968925),
    (118.721190, 31.969526), (118.721030, 31.969556),
    (118.720500, 31.969167), (118.719749, 31.968548),
    (118.718612, 31.967201), (118.718541, 31.967086),
    (118.718129, 31.966409), (118.717346, 31.965390),
    (118.717356, 31.965217), (118.718204, 31.964698),
    (118.718772, 31.964989), (118.719502, 31.965727),
    (118.719882, 31.966214), (118.720069, 31.966421),
    (118.720558, 31.966969),
]


def resample(points, count):
    lengths = [0.0]
    for (lon0, lat0), (lon1, lat1) in zip(points, points[1:]):
        lengths.append(lengths[-1] + hypot((lon1 - lon0) * 0.86, lat1 - lat0))
    total = lengths[-1]
    result = []
    for n in range(count):
        target = total * n / (count - 1)
        idx = next((i for i in range(1, len(lengths)) if lengths[i] >= target), len(lengths) - 1)
        span = lengths[idx] - lengths[idx - 1]
        ratio = 0.0 if span == 0 else (target - lengths[idx - 1]) / span
        lon0, lat0 = points[idx - 1]
        lon1, lat1 = points[idx]
        result.append((lon0 + (lon1 - lon0) * ratio, lat0 + (lat1 - lat0) * ratio))
    return result


def make_heart_rates(count):
    """Create reproducible, visibly fluctuating 90..150 bpm samples."""
    rng = random.Random(20260807)
    value = 108
    result = []
    for index in range(count):
        value += rng.choice((-4, -3, -2, -1, 1, 2, 3, 4))
        if index % 180 == 0:
            value += rng.choice((-10, -7, 7, 10))
        value = max(90, min(150, value))
        result.append(value)
    if count > 120:
        result[120] = 90
    if count > 1320:
        result[1320] = 150
    return result


def main():
    root = Element("gpx", {
        "version": "1.1",
        "creator": "Codex synthetic sport record generator",
        "xmlns": "http://www.topografix.com/GPX/1/1",
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "xmlns:gpxtpx": "http://www.garmin.com/xmlschemas/TrackPointExtension/v1",
    })
    metadata = SubElement(root, "metadata")
    SubElement(metadata, "name").text = "户外徒步：新城科技园A3栋—油坊桥"
    SubElement(metadata, "time").text = START.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    track = SubElement(root, "trk")
    SubElement(track, "name").text = "户外徒步"
    SubElement(track, "type").text = "Hiking"
    segment = SubElement(track, "trkseg")
    samples = resample(ROUTE, 3601)
    # Deterministic pseudo-random-looking HR values, all within 90..150 bpm.
    heart_rates = make_heart_rates(len(samples))
    for n, ((lon, lat), hr) in enumerate(zip(samples, heart_rates)):
        point = SubElement(segment, "trkpt", {"lat": f"{lat:.6f}", "lon": f"{lon:.6f}"})
        SubElement(point, "ele").text = "12"
        SubElement(point, "time").text = (START + timedelta(seconds=n)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        extensions = SubElement(point, "extensions")
        tpx = SubElement(extensions, "gpxtpx:TrackPointExtension")
        SubElement(tpx, "gpxtpx:hr").text = str(hr)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    ElementTree(root).write(OUT, encoding="utf-8", xml_declaration=True)
    print(f"wrote {OUT} ({len(samples)} points, HR {min(heart_rates)}..{max(heart_rates)} bpm)")


if __name__ == "__main__":
    main()
