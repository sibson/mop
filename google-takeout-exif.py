#!/usr/bin/env python3
"""Write a Google Takeout JSON sidecar's photoTakenTime and GPS coordinates
into the paired media file's EXIF, via exiftool, so date-based tools
(ingest.sh) and any GPS-aware viewer see normal EXIF without needing to
parse Google's JSON schema themselves.

Usage:
    python3 google-takeout-exif.py --dry-run DIRECTORY
    python3 google-takeout-exif.py DIRECTORY

Only touches files that have a <name>.<ext>.json sidecar next to them.
Date and GPS are checked and written independently: an existing date is
never overwritten, and a file with a date but no GPS still gets its
coordinates filled in (and vice versa). exiftool's default backup
(<file>_original) is left in place unless --no-backup is passed.
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

EXISTING_DATE_TAGS = ["-DateTimeOriginal", "-CreateDate", "-MediaCreateDate"]
EXISTING_GPS_TAGS = ["-GPSLatitude", "-GPSLongitude"]


def find_pairs(root):
    """Yield (media_path, json_path) for every file with a matching
    Google Takeout <name>.<ext>.json sidecar (skips folder-level
    metadata.json/metadata(N).json, which describe an album, not a file)."""
    for json_path in Path(root).rglob("*.json"):
        if json_path.stem.startswith("metadata"):
            continue
        media_path = json_path.with_suffix("")
        if media_path.is_file():
            yield media_path, json_path


def load_sidecar(json_path):
    try:
        return json.loads(json_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def taken_time(data):
    for key in ("photoTakenTime", "creationTime"):
        ts = data.get(key, {}).get("timestamp")
        if ts:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc)
    return None


def gps_coords(data):
    """Returns (lat, lon, alt) or None. Google emits (0, 0, 0) for photos
    with no location -- treat that as absent rather than null island."""
    for key in ("geoData", "geoDataExif"):
        geo = data.get(key)
        if not geo:
            continue
        lat, lon = geo.get("latitude"), geo.get("longitude")
        if lat and lon:
            return lat, lon, geo.get("altitude") or None
    return None


def has_any_tag(media_path, tags):
    result = subprocess.run(
        ["exiftool", "-s", "-s", "-s", *tags, str(media_path)],
        capture_output=True, text=True,
    )
    return bool(result.stdout.strip())


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("directory")
    ap.add_argument("--dry-run", action="store_true",
                     help="print what would change, write nothing")
    ap.add_argument("--no-backup", action="store_true",
                     help="pass exiftool -overwrite_original (skip the *_original backup file)")
    args = ap.parse_args()

    date_set = gps_set = date_skipped_has = gps_skipped_has = skipped_no_data = failed = 0

    for media_path, json_path in find_pairs(args.directory):
        data = load_sidecar(json_path)
        if data is None:
            skipped_no_data += 1
            continue

        cmd_args = []

        if has_any_tag(media_path, EXISTING_DATE_TAGS):
            date_skipped_has += 1
        else:
            dt = taken_time(data)
            if dt is not None:
                exif_date = dt.strftime("%Y:%m:%d %H:%M:%S")
                cmd_args.append(f"-AllDates={exif_date}")

        if has_any_tag(media_path, EXISTING_GPS_TAGS):
            gps_skipped_has += 1
        else:
            coords = gps_coords(data)
            if coords is not None:
                lat, lon, alt = coords
                cmd_args += [f"-GPSLatitude={lat}", f"-GPSLongitude={lon}"]
                if alt is not None:
                    cmd_args.append(f"-GPSAltitude={alt}")

        if not cmd_args:
            continue

        if args.dry_run:
            print(f"would set {media_path}: {' '.join(cmd_args)}")
            for a in cmd_args:
                if a.startswith("-AllDates"):
                    date_set += 1
                elif a.startswith("-GPSLatitude"):
                    gps_set += 1
            continue

        cmd = ["exiftool", *cmd_args]
        if args.no_backup:
            cmd.append("-overwrite_original")
        cmd.append(str(media_path))
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"FAILED {media_path}: {result.stderr.strip()}", file=sys.stderr)
            failed += 1
            continue
        for a in cmd_args:
            if a.startswith("-AllDates"):
                date_set += 1
            elif a.startswith("-GPSLatitude"):
                gps_set += 1
        print(f"set {media_path}: {' '.join(cmd_args)}")

    verb = "would be " if args.dry_run else ""
    print(f"\ndate: {date_set} {verb}set, {date_skipped_has} already had one")
    print(f"gps:  {gps_set} {verb}set, {gps_skipped_has} already had one")
    print(f"{skipped_no_data} sidecar(s) unreadable, {failed} exiftool failure(s)")


if __name__ == "__main__":
    main()
