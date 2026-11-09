#!/usr/bin/env python3
"""Validate generated sport-record files against the sport_record_save.c formats."""

import struct
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "configs/1786086000")
REC = ROOT / "1786086000"
ok = True


def check(name, cond, detail=""):
    global ok
    status = "OK " if cond else "FAIL"
    if not cond:
        ok = False
    print(f"[{status}] {name} {detail}")


# ---------------------------------------------------------------- summary.data
data = (REC / "summary.data").read_bytes()
check("summary.data prefix", data[:2] == b"\x00\x80", f"head={data[:2].hex()}")
pos = 2
fields = {}

def rv_at(p):
    v, s = 0, 0
    while True:
        b = data[p]; p += 1
        v |= (b & 0x7F) << s
        if not (b & 0x80):
            break
        s += 7
    return v, p

while pos < len(data):
    tag, pos = rv_at(pos)
    f = tag >> 3
    wire = tag & 7
    if wire == 0:
        val, pos = rv_at(pos)
        fields.setdefault(f, []).append(val)
    elif wire == 2:
        ln, pos = rv_at(pos)
        fields.setdefault(f, []).append(data[pos:pos + ln])
        pos += ln
    elif wire == 5:
        fields.setdefault(f, []).append(data[pos:pos + 4])
        pos += 4
    else:
        check(f"summary field {f} wire", False)
        break
for f in (1, 2, 3, 4, 7, 10, 11, 16, 19, 21, 22, 25, 53):
    check(f"summary field {f}", f in fields)
check("summary version", fields.get(1) and fields[1][0] == b"2.1")

# ---------------------------------------------------------------- detail.data
data = (REC / "detail.data").read_bytes()
pos = 0
tags = {}
detail_gps = []
detail_hr = []
while pos < len(data):
    tag = data[pos]
    if tag in (0x1F,):                      # two-byte tag 0x091F
        tag = struct.unpack_from("<H", data, pos)[0]
        ln = data[pos + 2]
        tags[tag] = tags.get(tag, 0) + 1
        pos += 3 + ln
    else:
        ln = data[pos + 1]
        tags[tag] = tags.get(tag, 0) + 1
        if tag == 2:
            # tag 2: relative time, gps time, lng, lat, altitude, speed...
            detail_gps.append(struct.unpack_from("<BBH I i i", data, pos))
        elif tag == 8:
            detail_hr.append(data[pos + 4])
        pos += 2 + ln
check("detail.data parses fully", pos == len(data),
      f"end={pos} len={len(data)} tags={tags}")
for t in (1, 2, 4, 5, 8, 11):
    check(f"detail tag {t:#x}", tags.get(t, 0) > 0, f"count={tags.get(t, 0)}")
check("detail sec-level tag", tags.get(0x091F, 0) > 0, f"count={tags.get(0x091F, 0)}")
check("detail GPS nodes present", len(detail_gps) > 0, f"count={len(detail_gps)}")
check("detail HR nodes present", len(detail_hr) > 0,
      f"count={len(detail_hr)} range={min(detail_hr) if detail_hr else '-'}..{max(detail_hr) if detail_hr else '-'}")

# ---------------------------------------------------------------- gps.data
data = (REC / "gps.data").read_bytes()
check("gps.data 16B records", len(data) % 16 == 0, f"len={len(data)} n={len(data)//16}")
ts0, lng0, lat0 = struct.unpack_from("<Qii", data, 0)
check("gps ts == track start", ts0 == 1786086000000, f"ts0={ts0}")
check("gps lng in Nanjing range", 355_000_000 <= lng0 <= 358_000_000, f"lng={lng0} ({lng0/3e6:.6f})")
check("gps lat in Nanjing range", 95_000_000 <= lat0 <= 97_000_000, f"lat={lat0} ({lat0/3e6:.6f})")
if detail_gps:
    check("detail GPS first matches gps.data",
          detail_gps[0][5] == lat0 and detail_gps[0][4] == lng0,
          f"detail=({detail_gps[0][4]},{detail_gps[0][5]}) gps=({lng0},{lat0})")
    ts_last, lng_last, lat_last = struct.unpack_from("<Qii", data, len(data) - 16)
    check("detail GPS last matches gps.data",
          detail_gps[-1][5] == lat_last and detail_gps[-1][4] == lng_last,
          f"detail=({detail_gps[-1][4]},{detail_gps[-1][5]}) gps=({lng_last},{lat_last})")

# ---------------------------------------------------------------- event.data
data = (REC / "event.data").read_bytes()
check("event.data 16B records", len(data) % 16 == 0, f"len={len(data)} n={len(data)//16}")
ev = [struct.unpack_from("<QQ", data, i * 16) for i in range(len(data) // 16)]
check("event start=1 stop=6", ev[0][1] == 1 and ev[-1][1] == 6, f"events={[e[1] for e in ev]}")

# ---------------------------------------------------------------- lap.data
data = (REC / "lap.data").read_bytes()
check("lap.data 112B records", len(data) % 112 == 0,
      f"len={len(data)} n={len(data)//112} (expect 112B/record)")
if len(data) % 112 == 0:
    for i in range(len(data) // 112):
        off = i * 112
        ts, = struct.unpack_from("<Q", data, off)
        tag, ln = data[off + 8], data[off + 9]
        idx, = struct.unpack_from("<H", data, off + 12)
        dist, = struct.unpack_from("<I", data, off + 22)
        check(f"lap {i + 1} node", tag == 11 and ln == 99 and idx == i + 1,
              f"tag={tag} len={ln} idx={idx} ts_rel={(ts-1786086000000)/1000:.0f}s dist={dist}")

# ---------------------------------------------------------------- hr / speed charts
for name in ("hr", "speed"):
    chart = struct.unpack("<I5f", (REC / f"{name}.chart").read_bytes())
    d = (REC / f"{name}.data").read_bytes()
    check(f"{name}.chart 24B", len(chart) == 6, f"{chart}")
    check(f"{name}.data 2400B int16", len(d) == 2400, f"len={len(d)}")
    vals = struct.unpack(f"<{len(d)//2}h", d)
    if name == "hr":
        check("hr.data in [0..1000]", min(vals) >= 0 and max(vals) <= 1000,
              f"min={min(vals)} max={max(vals)}")
        hr_min = chart[3]  # max value
        hr_max = chart[4]
        print(f"    hr.chart: dataMax={chart[1]} dataMin={chart[2]} "
              f"max={chart[3]} min={chart[4]} avg={chart[5]:.1f}")
        # verify inverse mapping: value -> hr stays within 90..150
        hrs = [chart[2] + v * (chart[1] - chart[2]) / 1000 for v in vals]
        print(f"    hr inverse: {min(hrs):.0f}..{max(hrs):.0f} bpm (target 90..150)")
    else:
        print(f"    speed.chart: dataMax={chart[1]} dataMin={chart[2]} "
              f"max={chart[3]} min={chart[4]} avg={chart[5]:.2f}")
        # chart dataMax/dataMin are max_level/min_level /100 (dm/h), pos = speed*1000/range
        kmh = [v * chart[1] / 100000 for v in vals]
        print(f"    speed inverse: {min(kmh):.1f}..{max(kmh):.1f} km/h (hiking ~4.5-8.5)")

# ---------------------------------------------------------------- managers
data = (Path("configs") / "rec.manager").read_bytes()
count, = struct.unpack_from("<H", data, 0)
check("rec.manager 2004B count=1", len(data) == 2004 and count == 1, f"count={count}")
if count:
    sync, tz, stype, stime = struct.unpack_from("<BbHI", data, 4)
    main_data, track_id = struct.unpack_from("<fI", data, 12)
    check("rec.manager entry", stype == 15 and track_id == 1786086000,
          f"type={stype} time={stime}s track={track_id} dist={main_data:.0f}m sync=0x{sync:02x}")

data = (Path("configs") / "total.info").read_bytes()
dist, cal, cnt, t = struct.unpack_from("<IIII", data, 0)
check("total.info 64B", len(data) == 64 and cnt == 1,
      f"dist={dist} cal={cal} count={cnt} time={t}")

data = (REC.parent / "child.manager").read_bytes()
tt, td, tc, avg_hr, child_num = struct.unpack_from("<IIIHH", data, 0)
check("child.manager 504B", len(data) == 504 and child_num == 1,
      f"total_time={tt} total_dist={td} consume={tc} avg_hr={avg_hr} child_num={child_num}")
ctype, ctrack, ctime, cdist = struct.unpack_from("<IIII", data, 64)
check("child.manager child", ctype == 15 and ctrack == 1786086000,
      f"type={ctype} track={ctrack} time={ctime} dist={cdist}")

print()
print("ALL OK" if ok else "HAS FAILURES")
