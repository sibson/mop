#!/usr/bin/env python3
"""Generate a self-contained HTML page comparing files in a directory
against content already present in a mop archive.db -- a quick visual
sanity check on ingest.sh's dedup decisions before trusting them.

Usage:
    python3 duplicate-review.py ARCHIVE_DB DIRECTORY [--out FILE]

For every file under DIRECTORY whose sha1 already exists in ARCHIVE_DB,
the report shows the two files side by side (thumbnail, path, size).
Images get a real thumbnail; other file types (video, docs) show a plain
row with no preview -- ffmpeg isn't part of this tool's dependencies.
The output HTML embeds thumbnails as data URIs, so it's one file you can
open directly, no server needed.
"""

import argparse
import base64
import hashlib
import html
import io
import sqlite3
from pathlib import Path
from urllib.parse import unquote, urlparse

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".heic", ".bmp", ".tiff", ".tif", ".webp"}
THUMB_SIZE = (240, 240)


def uri_to_path(uri):
    return Path(unquote(urlparse(uri).path))


def sha1_of_file(path, chunk_size=1 << 20):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def iter_files(root):
    for p in Path(root).rglob("*"):
        if p.is_file() and not p.name.startswith("."):
            yield p


def make_thumb_data_uri(path):
    if path.suffix.lower() not in IMAGE_EXTS:
        return None
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail(THUMB_SIZE)
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=70)
            return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def lookup_sha1(conn, sha1):
    return [row[0] for row in conn.execute("SELECT uri FROM files WHERE sha1 = ?", (sha1,))]


def render_side(path, thumb, extra):
    thumb_html = f'<img src="{thumb}">' if thumb else f'<div class="noThumb">no preview<br>{html.escape(path.suffix or "(no ext)")}</div>'
    return f"""
    <div class="side">
      <div class="thumb">{thumb_html}</div>
      <div class="path">{html.escape(str(path))}</div>
      <div class="meta">{extra}</div>
    </div>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("archive_db")
    ap.add_argument("directory")
    ap.add_argument("--out", default="duplicate-review.html")
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{args.archive_db}?mode=ro", uri=True)

    total = 0
    pairs = []
    for path in iter_files(args.directory):
        total += 1
        sha1 = sha1_of_file(path)
        archived_uris = lookup_sha1(conn, sha1)
        if archived_uris:
            pairs.append((path, sha1, archived_uris))
    conn.close()

    rows = []
    for path, sha1, archived_uris in pairs:
        arch_path = uri_to_path(archived_uris[0])
        src_thumb = make_thumb_data_uri(path)
        arch_thumb = make_thumb_data_uri(arch_path)
        try:
            size = f"{path.stat().st_size:,} bytes"
        except OSError:
            size = "?"
        extra = f"{len(archived_uris)} archive match(es)" if len(archived_uris) > 1 else "archived"
        rows.append(f"""
        <div class="pair">
          {render_side(path, src_thumb, size)}
          {render_side(arch_path, arch_thumb, extra)}
          <div class="sha">{sha1}</div>
        </div>""")

    out_html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Duplicate review: {html.escape(str(args.directory))}</title>
<style>
body {{ font-family: -apple-system, sans-serif; background:#111; color:#eee; margin:0; padding:20px; }}
h1 {{ font-size: 16px; font-weight: 600; }}
.summary {{ color:#999; margin-bottom: 20px; font-size: 13px; }}
.pair {{ display:flex; gap:20px; align-items:flex-start; border-bottom:1px solid #333; padding:16px 0; }}
.side {{ flex:1; min-width:0; }}
.thumb {{ width:240px; height:240px; display:flex; align-items:center; justify-content:center; background:#222; border-radius:6px; overflow:hidden; }}
.thumb img {{ max-width:100%; max-height:100%; object-fit:contain; }}
.noThumb {{ color:#666; font-size:12px; text-align:center; }}
.path {{ font-size:11px; color:#ccc; margin-top:6px; word-break:break-all; }}
.meta {{ font-size:11px; color:#888; margin-top:2px; }}
.sha {{ font-size:10px; color:#555; align-self:center; width:90px; word-break:break-all; }}
</style></head>
<body>
<h1>Duplicate review: {html.escape(str(args.directory))}</h1>
<div class="summary">{len(pairs)} of {total} files already match something in the archive. Left = your file, right = archived match.</div>
{''.join(rows) if rows else '<p style="color:#999">No matches found -- nothing in this directory duplicates the archive.</p>'}
</body></html>
"""
    Path(args.out).write_text(out_html)
    print(f"{len(pairs)}/{total} matched archived content. Wrote {args.out}")


if __name__ == "__main__":
    main()
