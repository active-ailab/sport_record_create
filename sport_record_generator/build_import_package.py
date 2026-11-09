#!/usr/bin/env python3
"""Assemble the importable sport-record package + ZIP for the watch.

Layout mirrors /home/hgw/shaolu/sport_record (the reference):
  sport_record_hike_20260807_1500_1600/
  |-- rec.manager
  |-- total.info
  |-- 1786086000/
  |   |-- child.manager
  |   `-- 1786086000/
  |       |-- summary.data  detail.data  gps.data  event.data  lap.data
  |       |-- special.data  hr.data      hr.chart  speed.data  speed.chart
"""

import shutil
import zipfile
from pathlib import Path

from generate_native_hike_record import TRACK_ID, OUT

SRC = Path("configs") / str(TRACK_ID)          # configs/1786086000/
DEST = Path("/home/hgw/shaolu") / f"sport_record_hike_20260807_1500_1600"


def main():
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)

    # top-level index files
    shutil.copy2(Path("configs") / "rec.manager", DEST / "rec.manager")
    shutil.copy2(Path("configs") / "total.info", DEST / "total.info")

    # record folder: <track_id>/child.manager + <track_id>/<track_id>/*.data
    rec_folder = DEST / str(TRACK_ID)
    (rec_folder / str(TRACK_ID)).mkdir(parents=True)
    shutil.copy2(SRC / "child.manager", rec_folder / "child.manager")
    for name in ("summary.data", "detail.data", "gps.data", "event.data",
                 "lap.data", "special.data", "hr.data", "hr.chart",
                 "speed.data", "speed.chart"):
        shutil.copy2(OUT / name, rec_folder / str(TRACK_ID) / name)

    # ZIP
    zip_path = Path("/home/hgw/shaolu") / "sport_record_hike_20260807_1500_1600.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(DEST.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(DEST.parent))
    print(f"package: {DEST}")
    print(f"zip: {zip_path} ({zip_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
