# NEW/ Ingest Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `ingest.sh`, a shell script that deduplicates a raw-dump directory (e.g. `NEW/`) against the full Memories archive by content hash and files net-new content into the correct `YYYY/` folder by capture date, using only mop's existing CLI (`mopcli.py`) — no new mop code.

**Architecture:** One linear bash script, driven entirely by `mopcli.py local`/`mopcli.py store` subcommands for all hashing and comparison, plus `exiftool`/`jq` for date extraction. See [`docs/superpowers/specs/2026-08-26-mop-ingest-design.md`](../specs/2026-08-26-mop-ingest-design.md) for the full design and rationale.

**Tech Stack:** bash, `mopcli.py` (this repo, run via `uv run --project`), `exiftool`, `jq`, `sqlite3` (for test verification only).

## Global Constraints

- **No changes to `local.py`/`store.py`/`mopcli.py`.** Every operation the script needs already exists (`local index`, `local trash`, `store mk`, `store ls`, `store present`, `store missing`). This is a hard constraint from the spec, not a suggestion.
- **`ingest.sh` is NOT tracked in this git repo and is not committed.** Per an explicit decision during design, it's developed and lives directly at the Memories share root (untracked there, matching how `cleanup-photos.sh` already exists). Tasks below build and verify it in a local scratch location (`/tmp/mop-ingest-fixture`) using manual run-and-inspect verification instead of `pytest`/git-commit steps. There is a final task to place the finished script at its real destination.
- **Portable across bash 3.2 and bash 4+.** This Mac's `/usr/bin/env bash` resolves to bash 3.2.57 (confirmed); treasurebox (the eventual host) is Debian 11 with a newer bash. Bash 3.2 throws `unbound variable` when expanding an empty array under `set -u` (confirmed by direct test: `set -euo pipefail; arr=(); echo "${arr[@]}"` fails on this Mac's bash). Therefore: **no bash arrays, no `mapfile`/`readarray`, and `set -eo pipefail` without `-u`.** Use plain newline-accumulated strings plus `sort -u` wherever the design's prose describes "a list" or "distinct values."
- **`uv run --project DIR SCRIPT_PATH`** is the verified invocation form for calling mop from outside its own directory (confirmed: `uv run --project /Users/marcs/src/mop /Users/marcs/src/mop/mopcli.py --help` works from `/tmp`). `mopcli.py` wraps `local`/`store`/`b2` as subcommands (confirmed via `mopcli.py`'s source: `cli.add_command(local_cli, 'local')`, `cli.add_command(store.cli, 'store')`).
- **`store ls`, not `store dupes`, for internal-duplicate detection.** `store dupes` joins multiple URIs per group with a plain space, and this share's real paths contain literal, non-percent-encoded spaces (confirmed by direct test against a `Wedding Videos/space name.jpg`-style fixture — the joined output becomes ambiguous to split). `store ls` prints one `sha1<TAB>uri` per line, sorted by sha1, and is safe to group by adjacent-line comparison.
- **Both `archive.db` and `new.db` must exist with schema before first use.** `store present`/`store missing`/`store ls` call `sqlite3.connect(dbpath)` without `create=True` — on a totally fresh path this creates an empty *file* but not the `files` table, and the next query fails with `no such table: files` (confirmed: `store mk PATH` is the existing command that creates an empty schema'd db and is idempotent-safe to guard with a `[ -f ... ] ||` check).
- **The final archive-reindex step (step 6) must be scoped to the year folders actually touched this run — never the whole archive root.** Reindexing the whole root would also walk `.trash/` and `.ingest/` (mop's own db files), hashing trashed content into the archive index. That's a correctness bug: a later legitimate arrival identical to something already trashed would then be wrongly flagged as already-archived and deduped away, even though no live copy exists.
- **Every trash goes through `mop local trash`**, never a raw `rm` or `mv` to somewhere ad hoc — this is what gives every removal a `.trash/`+`trashed.log` recovery trail.

---

### Task 1: Scaffold — arg parsing, dependency checks, DB bootstrap, and Step 1 (index target)

**Files:**
- Create: `/tmp/mop-ingest-fixture/ingest.sh` (scratch development copy — see Task 8 for placing the real one)

**Interfaces:**
- Produces: the script accepts `[--dry-run] PATH` as its only arguments; sets `$DRY_RUN` (`true`/`false` string, compared with `[ "$DRY_RUN" = true ]`), `$ROOT` (absolute path to the script's own directory), `$MOP_DIR` (default `$HOME/src/mop`, overridable via env var of the same name), `$ARCHIVE_DB`/`$NEW_DB` (`$ROOT/.ingest/archive.db` / `.../new.db`), and a `mop()` shell function wrapping `uv run --project "$MOP_DIR" "$MOP_DIR/mopcli.py"`. Later tasks append to this same file, after the `echo "== Step 1..."` block and before nothing else exists yet.

- [ ] **Step 1: Write the scaffold**

Create `/tmp/mop-ingest-fixture/ingest.sh`:

```bash
#!/usr/bin/env bash
# Portable across bash 3.2 (macOS default) and bash 4+ (Debian) —
# no arrays, no mapfile/readarray, no set -u (empty-array expansion under
# set -u throws "unbound variable" on bash <4.4, which macOS ships).
set -eo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
MOP_DIR="${MOP_DIR:-$HOME/src/mop}"
ARCHIVE_DB="$ROOT/.ingest/archive.db"
NEW_DB="$ROOT/.ingest/new.db"

DRY_RUN=false
INGEST_PATH=""
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    *) INGEST_PATH="$arg" ;;
  esac
done

if [ -z "$INGEST_PATH" ]; then
  echo "usage: $0 [--dry-run] PATH" >&2
  exit 1
fi

case "$INGEST_PATH" in
  /*) ;;
  *) INGEST_PATH="$ROOT/$INGEST_PATH" ;;
esac

command -v uv >/dev/null || { echo "uv not found" >&2; exit 1; }
command -v exiftool >/dev/null || { echo "exiftool not found" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq not found" >&2; exit 1; }

mop() {
  uv run --project "$MOP_DIR" "$MOP_DIR/mopcli.py" "$@"
}

trash_flag=""
if [ "$DRY_RUN" = true ]; then
  trash_flag="--dry-run"
fi

mkdir -p "$ROOT/.ingest"
[ -f "$ARCHIVE_DB" ] || mop store mk "$ARCHIVE_DB"
[ -f "$NEW_DB" ] || mop store mk "$NEW_DB"

echo "== Step 1: indexing $INGEST_PATH =="
mop local index --dbpath "$NEW_DB" --skip-existing "$INGEST_PATH"
```

Then: `chmod +x /tmp/mop-ingest-fixture/ingest.sh`

- [ ] **Step 2: Build a fixture and run it**

```bash
rm -rf /tmp/mop-ingest-fixture-data
mkdir -p /tmp/mop-ingest-fixture-data/NEW
echo one > /tmp/mop-ingest-fixture-data/NEW/one.txt
echo two > /tmp/mop-ingest-fixture-data/NEW/two.txt
cp /tmp/mop-ingest-fixture/ingest.sh /tmp/mop-ingest-fixture-data/ingest.sh
cd /tmp/mop-ingest-fixture-data
./ingest.sh NEW
```

Expected output (hashes will differ, that's fine — just confirm two `sha filename` lines and no errors):
```
== Step 1: indexing /tmp/mop-ingest-fixture-data/NEW ==
<sha1> one.txt
<sha1> two.txt
```

- [ ] **Step 3: Verify the index and path-resolution behavior**

```bash
sqlite3 /tmp/mop-ingest-fixture-data/.ingest/new.db "SELECT count(*) FROM files;"
```
Expected: `2`

Now confirm the relative-path resolution (the `case "$INGEST_PATH" in /*) ;; *) ...` block) works regardless of caller's cwd:
```bash
cd /tmp
/tmp/mop-ingest-fixture-data/ingest.sh NEW
```
Expected: no error, `Step 1` line shows the full resolved path `== Step 1: indexing /tmp/mop-ingest-fixture-data/NEW ==` even though cwd was `/tmp`, not the fixture dir.

- [ ] **Step 4: Confirm behavior matches expected output above before moving on.**

---

### Task 2: Step 2 — dedupe within the batch (`store ls` + adjacent-line grouping)

**Files:**
- Modify: `/tmp/mop-ingest-fixture/ingest.sh` — append after the Task 1 block

**Interfaces:**
- Consumes: `$NEW_DB`, `$ROOT`, `$trash_flag`, `mop()` from Task 1.
- Produces: no new variables consumed by later tasks.

- [ ] **Step 1: Append the internal-dedupe block**

```bash

echo "== Step 2: deduping within batch =="
prev_sha=""
dupe_uris=""
while IFS=$'\t' read -r sha uri; do
  if [ "$sha" = "$prev_sha" ]; then
    dupe_uris="$dupe_uris$uri"$'\n'
  fi
  prev_sha="$sha"
done < <(mop store ls "$NEW_DB")
if [ -n "$dupe_uris" ]; then
  printf '%s' "$dupe_uris" | mop local trash --root "$ROOT" $trash_flag -
else
  echo "no internal duplicates"
fi
```

- [ ] **Step 2: Build a fixture with an internal duplicate (including a space in the path, since that's what broke `store dupes`) and run it**

```bash
rm -rf /tmp/mop-ingest-fixture-data
mkdir -p "/tmp/mop-ingest-fixture-data/NEW/Wedding Videos"
echo same-content > /tmp/mop-ingest-fixture-data/NEW/twin-a.txt
cp /tmp/mop-ingest-fixture-data/NEW/twin-a.txt "/tmp/mop-ingest-fixture-data/NEW/Wedding Videos/twin-b.txt"
echo unique > /tmp/mop-ingest-fixture-data/NEW/solo.txt
cp /tmp/mop-ingest-fixture/ingest.sh /tmp/mop-ingest-fixture-data/ingest.sh
cd /tmp/mop-ingest-fixture-data
./ingest.sh NEW
```

Expected output includes:
```
== Step 2: deduping within batch ==
trashed: NEW/Wedding Videos/twin-b.txt
1 trashed, 0 skipped, 0 errored
```
(Which of the two identical files is "kept" vs "trashed" depends on row order within the sha1 group (insertion order, not URI order) — confirm exactly one of `twin-a.txt`/`twin-b.txt` was trashed, not both, not neither.)

- [ ] **Step 3: Verify the file layout and trash log**

```bash
find /tmp/mop-ingest-fixture-data -name "twin-*"
cat /tmp/mop-ingest-fixture-data/.trash/trashed.log
```
Expected: one twin file remains under `NEW/`, the other is under `.trash/NEW/...`, and `trashed.log` has one line recording the move with a relative path containing the space (e.g. `NEW/Wedding Videos/twin-b.txt`) — confirming the space-containing path round-tripped correctly with no misparse.

- [ ] **Step 4: Confirm behavior matches expected output above before moving on.**

---

### Task 3: Step 3 — dedupe against the archive

**Files:**
- Modify: `/tmp/mop-ingest-fixture/ingest.sh` — append after the Task 2 block

**Interfaces:**
- Consumes: `$ARCHIVE_DB`, `$NEW_DB`, `$ROOT`, `$trash_flag`, `mop()`.
- Produces: no new variables consumed by later tasks.

- [ ] **Step 1: Append the archive-dedupe block**

```bash

echo "== Step 3: deduping against archive =="
present_uris=$(mop store present "$ARCHIVE_DB" "$NEW_DB" | cut -f2)
if [ -n "$present_uris" ]; then
  printf '%s\n' "$present_uris" | mop local trash --root "$ROOT" $trash_flag -
else
  echo "no archive duplicates"
fi
```

- [ ] **Step 2: Build a fixture with a pre-existing archive and a duplicate arrival, run it**

```bash
rm -rf /tmp/mop-ingest-fixture-data
mkdir -p /tmp/mop-ingest-fixture-data/NEW /tmp/mop-ingest-fixture-data/archive-src/2020 /tmp/mop-ingest-fixture-data/.ingest
echo archived-content > /tmp/mop-ingest-fixture-data/archive-src/2020/existing.txt
cp /tmp/mop-ingest-fixture-data/archive-src/2020/existing.txt /tmp/mop-ingest-fixture-data/NEW/already-archived.txt
echo brand-new > /tmp/mop-ingest-fixture-data/NEW/genuinely-new.txt
cp /tmp/mop-ingest-fixture/ingest.sh /tmp/mop-ingest-fixture-data/ingest.sh

cd /tmp/mop-ingest-fixture-data
uv run --project "$HOME/src/mop" "$HOME/src/mop/mopcli.py" store mk .ingest/archive.db
uv run --project "$HOME/src/mop" "$HOME/src/mop/mopcli.py" local index --dbpath .ingest/archive.db archive-src
./ingest.sh NEW
```

Expected output includes:
```
== Step 3: deduping against archive ==
trashed: NEW/already-archived.txt
1 trashed, 0 skipped, 0 errored
```

- [ ] **Step 3: Verify**

```bash
find /tmp/mop-ingest-fixture-data/NEW /tmp/mop-ingest-fixture-data/.trash -type f
```
Expected: `already-archived.txt` under `.trash/NEW/`, `genuinely-new.txt` still under `NEW/`.

- [ ] **Step 4: Confirm behavior matches expected output above before moving on.**

---

### Task 4: Steps 4/5 — date detection and placement into `YYYY/` (no collision handling yet)

**Files:**
- Modify: `/tmp/mop-ingest-fixture/ingest.sh` — append after the Task 3 block

**Interfaces:**
- Consumes: `$ARCHIVE_DB`, `$NEW_DB`, `$ROOT`, `$DRY_RUN`, `mop()`.
- Produces: `touched_years` (newline-accumulated string of years moved into this run) — consumed by Task 6.

- [ ] **Step 1: Append the placement block (destination path computed directly for now — Task 5 adds collision-safe naming)**

```bash

echo "== Step 4/5: placing net-new files =="
touched_years=""
while IFS= read -r uri; do
  [ -n "$uri" ] || continue
  src="${uri#file://}"
  [ -e "$src" ] || continue

  date=$(exiftool -json -DateTimeOriginal -CreateDate -MediaCreateDate "$src" \
    | jq -r '.[0].DateTimeOriginal // .[0].CreateDate // .[0].MediaCreateDate // empty')

  if [ -z "$date" ]; then
    echo "no date, leaving in place: $src"
    continue
  fi

  year="${date:0:4}"
  filename="$(basename "$src")"
  destdir="$ROOT/$year"
  dest="$destdir/$filename"

  if [ "$DRY_RUN" = true ]; then
    echo "would move: $src -> $dest"
  else
    mkdir -p "$destdir"
    mv "$src" "$dest"
    echo "moved: $src -> $dest"
  fi
  touched_years="$touched_years$year"$'\n'
done < <(mop store missing "$ARCHIVE_DB" "$NEW_DB" | cut -f2)
```

- [ ] **Step 2: Build a fixture with a dated photo, a `CreateDate`-only photo, and an undated photo, run it**

```bash
rm -rf /tmp/mop-ingest-fixture-data
mkdir -p /tmp/mop-ingest-fixture-data/NEW /tmp/mop-ingest-fixture-data/.ingest
python3 -c "
from PIL import Image
Image.new('RGB',(1,1),(30,30,30)).save('/tmp/mop-ingest-fixture-data/NEW/dated.jpg')
Image.new('RGB',(1,1),(40,40,40)).save('/tmp/mop-ingest-fixture-data/NEW/createdate-only.jpg')
Image.new('RGB',(1,1),(50,50,50)).save('/tmp/mop-ingest-fixture-data/NEW/nodate.jpg')
"
exiftool -overwrite_original -q "-DateTimeOriginal=2015:06:12 10:00:00" /tmp/mop-ingest-fixture-data/NEW/dated.jpg
exiftool -overwrite_original -q "-CreateDate=2018:03:01 09:00:00" /tmp/mop-ingest-fixture-data/NEW/createdate-only.jpg
cp /tmp/mop-ingest-fixture/ingest.sh /tmp/mop-ingest-fixture-data/ingest.sh

cd /tmp/mop-ingest-fixture-data
uv run --project "$HOME/src/mop" "$HOME/src/mop/mopcli.py" store mk .ingest/archive.db
./ingest.sh NEW
```

Expected output includes:
```
== Step 4/5: placing net-new files ==
moved: /tmp/mop-ingest-fixture-data/NEW/createdate-only.jpg -> /tmp/mop-ingest-fixture-data/2018/createdate-only.jpg
moved: /tmp/mop-ingest-fixture-data/NEW/dated.jpg -> /tmp/mop-ingest-fixture-data/2015/dated.jpg
no date, leaving in place: /tmp/mop-ingest-fixture-data/NEW/nodate.jpg
```
(Order of the two moved files may vary — both lines must appear.)

- [ ] **Step 3: Verify**

```bash
find /tmp/mop-ingest-fixture-data -name "*.jpg" | sort
```
Expected:
```
/tmp/mop-ingest-fixture-data/2015/dated.jpg
/tmp/mop-ingest-fixture-data/2018/createdate-only.jpg
/tmp/mop-ingest-fixture-data/NEW/nodate.jpg
```

- [ ] **Step 4: Confirm behavior matches expected output above before moving on.**

---

### Task 5: Collision-safe destination naming (`-1`, `-2`, ... suffix)

**Files:**
- Modify: `/tmp/mop-ingest-fixture/ingest.sh` — add the `next_free_name` function before the Task 4 block, and change `dest="$destdir/$filename"` to use it.

**Interfaces:**
- Produces: `next_free_name DIR BASE EXT` → echoes the first non-existent `DIR/BASE.EXT`, `DIR/BASE-1.EXT`, `DIR/BASE-2.EXT`, ... — used by the Task 4 block.

- [ ] **Step 1: Add the function, immediately before the `echo "== Step 4/5..."` line**

```bash

next_free_name() {
  dir="$1"; base="$2"; ext="$3"
  candidate="$dir/$base.$ext"
  n=1
  while [ -e "$candidate" ]; do
    candidate="$dir/${base}-${n}.$ext"
    n=$((n + 1))
  done
  echo "$candidate"
}
```

- [ ] **Step 2: Replace the destination computation inside the Task 4 loop**

Change:
```bash
  filename="$(basename "$src")"
  destdir="$ROOT/$year"
  dest="$destdir/$filename"
```
to:
```bash
  filename="$(basename "$src")"
  ext="${filename##*.}"
  base="${filename%.*}"
  destdir="$ROOT/$year"
  dest=$(next_free_name "$destdir" "$base" "$ext")
```

- [ ] **Step 3: Unit-test `next_free_name` in isolation**

```bash
bash -c '
next_free_name() {
  dir="$1"; base="$2"; ext="$3"
  candidate="$dir/$base.$ext"
  n=1
  while [ -e "$candidate" ]; do
    candidate="$dir/${base}-${n}.$ext"
    n=$((n + 1))
  done
  echo "$candidate"
}
mkdir -p /tmp/mop-collision-test
touch /tmp/mop-collision-test/photo.jpg /tmp/mop-collision-test/photo-1.jpg
next_free_name /tmp/mop-collision-test photo jpg
'
```
Expected: `/tmp/mop-collision-test/photo-2.jpg`

- [ ] **Step 4: Build a fixture where two different-content net-new files would collide on destination filename, run the full script**

```bash
rm -rf /tmp/mop-ingest-fixture-data /tmp/mop-collision-test
mkdir -p /tmp/mop-ingest-fixture-data/NEW/a /tmp/mop-ingest-fixture-data/NEW/b /tmp/mop-ingest-fixture-data/.ingest
python3 -c "
from PIL import Image
Image.new('RGB',(1,1),(70,70,70)).save('/tmp/mop-ingest-fixture-data/NEW/a/photo.jpg')
Image.new('RGB',(1,1),(80,80,80)).save('/tmp/mop-ingest-fixture-data/NEW/b/photo.jpg')
"
exiftool -overwrite_original -q "-DateTimeOriginal=2019:01:01 00:00:00" /tmp/mop-ingest-fixture-data/NEW/a/photo.jpg
exiftool -overwrite_original -q "-DateTimeOriginal=2019:01:01 00:00:00" /tmp/mop-ingest-fixture-data/NEW/b/photo.jpg
cp /tmp/mop-ingest-fixture/ingest.sh /tmp/mop-ingest-fixture-data/ingest.sh

cd /tmp/mop-ingest-fixture-data
uv run --project "$HOME/src/mop" "$HOME/src/mop/mopcli.py" store mk .ingest/archive.db
./ingest.sh NEW
find /tmp/mop-ingest-fixture-data/2019 -type f
```

Expected: two files in `2019/`: `photo.jpg` and `photo-1.jpg` — both preserved, neither overwritten (verify their pixel content still differs, e.g. `exiftool -FileSize` or just that both files exist with distinct byte content via `cmp`).

- [ ] **Step 5: Confirm behavior matches expected output above before moving on.**

---

### Task 6: Step 6 — fold moved files into the durable archive index (scoped to touched years)

**Files:**
- Modify: `/tmp/mop-ingest-fixture/ingest.sh` — append at the end of the file

**Interfaces:**
- Consumes: `$touched_years` (from Task 4), `$ARCHIVE_DB`, `$ROOT`, `$DRY_RUN`, `mop()`.

- [ ] **Step 1: Append the final block**

```bash

echo "== Step 6: folding moves into archive index =="
if [ "$DRY_RUN" = true ]; then
  echo "dry-run: skipping archive reindex"
elif [ -z "$touched_years" ]; then
  echo "no files moved, nothing to fold in"
else
  for year in $(printf '%s' "$touched_years" | sort -u); do
    mop local index --dbpath "$ARCHIVE_DB" --skip-existing "$ROOT/$year"
  done
fi

echo "done"
```

- [ ] **Step 2: Reuse the Task 5 fixture (already has files moved into `2019/`) and confirm the archive index picked them up, without walking `.trash`/`.ingest`**

```bash
cd /tmp/mop-ingest-fixture-data
sqlite3 .ingest/archive.db "SELECT path FROM files ORDER BY path;"
```
Expected: exactly the two `2019/photo*.jpg` paths — **no** rows for anything under `.trash/` or `.ingest/` (confirming the scoped reindex, not a whole-`$ROOT` walk).

- [ ] **Step 3: Confirm re-running is a fast no-op (idempotency)**

```bash
time ./ingest.sh NEW
```
Expected: completes in well under a second, `Step 1` reports the files as already-indexed-skipped, `Step 6` prints `no files moved, nothing to fold in` (nothing left in `NEW/` to move — both `photo.jpg`/`photo-1.jpg` already relocated).

- [ ] **Step 4: Confirm behavior matches expected output above before moving on.**

---

### Task 7: Full end-to-end acceptance test (matches design §6)

**Files:**
- None modified — this exercises the completed script from Task 6 as-is.

**Interfaces:**
- None — this is a black-box test of the finished script.

- [ ] **Step 1: Build the combined fixture** (archived duplicate, internal duplicate with a space in its path, dated photo, `CreateDate`-only photo, undated photo — the full scenario from the design doc's testing section)

```bash
rm -rf /tmp/mop-ingest-fixture-data
mkdir -p "/tmp/mop-ingest-fixture-data/NEW/Wedding Videos" /tmp/mop-ingest-fixture-data/archive-src/2020 /tmp/mop-ingest-fixture-data/.ingest
python3 -c "
from PIL import Image
Image.new('RGB',(1,1),(10,10,10)).save('/tmp/mop-ingest-fixture-data/archive-src/2020/existing.jpg')
Image.new('RGB',(1,1),(20,20,20)).save('/tmp/mop-ingest-fixture-data/archive-src/2020/existing2.jpg')
Image.new('RGB',(1,1),(30,30,30)).save('/tmp/mop-ingest-fixture-data/NEW/dated.jpg')
Image.new('RGB',(1,1),(40,40,40)).save('/tmp/mop-ingest-fixture-data/NEW/createdate-only.jpg')
Image.new('RGB',(1,1),(50,50,50)).save('/tmp/mop-ingest-fixture-data/NEW/nodate.jpg')
Image.new('RGB',(1,1),(60,60,60)).save('/tmp/mop-ingest-fixture-data/NEW/twin-a.jpg')
"
cp /tmp/mop-ingest-fixture-data/archive-src/2020/existing2.jpg /tmp/mop-ingest-fixture-data/NEW/already-archived.jpg
exiftool -overwrite_original -q "-DateTimeOriginal=2015:06:12 10:00:00" /tmp/mop-ingest-fixture-data/NEW/dated.jpg
exiftool -overwrite_original -q "-CreateDate=2018:03:01 09:00:00" /tmp/mop-ingest-fixture-data/NEW/createdate-only.jpg
cp /tmp/mop-ingest-fixture-data/NEW/twin-a.jpg "/tmp/mop-ingest-fixture-data/NEW/Wedding Videos/twin-b.jpg"
cp /tmp/mop-ingest-fixture/ingest.sh /tmp/mop-ingest-fixture-data/ingest.sh

cd /tmp/mop-ingest-fixture-data
uv run --project "$HOME/src/mop" "$HOME/src/mop/mopcli.py" store mk .ingest/archive.db
uv run --project "$HOME/src/mop" "$HOME/src/mop/mopcli.py" local index --dbpath .ingest/archive.db archive-src
```

- [ ] **Step 2: Dry run, confirm the printed plan covers every case**

```bash
./ingest.sh --dry-run NEW
```
Expected: one `would trash` for the internal twin, one `would trash` for `already-archived.jpg`, `would move` lines for `dated.jpg` (→ `2015/`) and `createdate-only.jpg` (→ `2018/`), `no date, leaving in place` for `nodate.jpg` and (in the dry-run's non-chained view) the not-yet-trashed twin. No files actually change:
```bash
find /tmp/mop-ingest-fixture-data/NEW -type f | sort
```
Expected: all 6 original files still present under `NEW/`.

- [ ] **Step 3: Real run, confirm final layout**

```bash
./ingest.sh NEW
find /tmp/mop-ingest-fixture-data -type f \( -name "*.jpg" \) -not -path "*/archive-src/*" | sort
```
Expected:
```
/tmp/mop-ingest-fixture-data/.trash/NEW/Wedding Videos/twin-b.jpg
/tmp/mop-ingest-fixture-data/.trash/NEW/already-archived.jpg
/tmp/mop-ingest-fixture-data/2015/dated.jpg
/tmp/mop-ingest-fixture-data/2018/createdate-only.jpg
/tmp/mop-ingest-fixture-data/NEW/nodate.jpg
/tmp/mop-ingest-fixture-data/NEW/twin-a.jpg
```
(the surviving twin may be `twin-a.jpg` or the one that was at `Wedding Videos/twin-b.jpg` depending on row insertion order (not URI order) — exactly one must survive, in `NEW/`, and one must be trashed)

- [ ] **Step 4: Re-run, confirm fast idempotent no-op**

```bash
time ./ingest.sh NEW
```
Expected: `Step 1` reports all remaining files already-indexed (no rehash), `Step 2`/`Step 3` report `SKIP missing` for the stale trashed/moved rows (harmless, per the design's accepted limitation), `Step 4/5` reports `no date, leaving in place` for the two still-undated files, `Step 6` reports `no files moved, nothing to fold in`. Total time well under a second (excluding `uv`/exiftool process-startup overhead).

- [ ] **Step 5: Confirm every expectation above held. This is the acceptance gate for the whole script — do not proceed to Task 8 if any of it diverged.**

---

### Task 8: Place the finished script at its real destination

**Files:**
- Create: `/Volumes/Memories/ingest.sh` (the mounted path to the Memories share root; on the treasurebox host itself this is `/srv/shares/memories/ingest.sh` — same file, different mount point)

**Interfaces:**
- None — this is a deployment step, not a code change.

- [ ] **Step 1: Copy the verified script to the real share root**

```bash
cp /tmp/mop-ingest-fixture/ingest.sh /Volumes/Memories/ingest.sh
chmod +x /Volumes/Memories/ingest.sh
```

- [ ] **Step 2: Dry run against the real `NEW/` directory** (read-only aside from building `.ingest/new.db`/`archive.db` index caches under `/Volumes/Memories/.ingest/` — additive, non-destructive, safe to delete and redo)

```bash
cd /Volumes/Memories
./ingest.sh --dry-run NEW
```

This will take a while the first time (`NEW/` is ~117 GB, and this is over the CIFS mount per the design's non-goals — full-speed operation on the treasurebox host is future work). Watch the output for anything unexpected: an unusually large number of `would trash` lines, dates that look implausible (e.g. the `2040`-dated files mentioned in past investigation of this share), or errors from `exiftool` on unfamiliar file types.

- [ ] **Step 3: Stop here. Do not run `./ingest.sh NEW` for real against production data as part of this plan.**

A live run against the real `NEW/` backlog moves and trashes a large number of real files. That is a deliberate action for you to trigger yourself once you've reviewed the dry-run output from Step 2 and are comfortable with it — not something to do automatically as the last step of an implementation plan.

---

## Summary

After Task 7, `ingest.sh` is fully built and verified against synthetic fixtures covering every behavior in the design: internal dedup (including space-containing paths), archive dedup, date-based filing via two different EXIF tag priorities, the undated-file flag, collision-safe naming, scoped incremental archive reindexing, and idempotent re-runs. Task 8 places it at its real location and validates it against real data in dry-run only. The first live run against `NEW/` is a follow-up action, by hand, once you're ready.
