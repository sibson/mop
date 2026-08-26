# NEW/ Ingest Pipeline — Design

## 0. Problem

`NEW/` on the Memories share accumulates raw dumps copied in from other
sources (phones, cameras, other people's drives, old backup drives). Two
things need to happen to each dump, repeatedly as new ones arrive:

1. **Deduplicate** against the full archive (the `YYYY/` year folders) by
   content, not name or path.
2. **File net-new content** into the correct `YYYY/` folder by capture date.

This must run incrementally — re-running against a `NEW/` that already
contains previously-flagged or already-processed material should be cheap,
not a full re-hash of everything.

## 1. Non-goals

- No changes to `~/src/treasurebox` or its ansible roles. Getting a working
  runtime (exiftool, uv/Python) onto the treasurebox host is a separate,
  later, manual step in that repo — out of scope here.
- No changes to mop's Python codebase (`local.py`, `store.py`, `mopcli.py`).
  Every operation below is an existing mop command; this design is pure
  orchestration.
- No B2/rclone changes. Files landing in `YYYY/` under the existing
  `$MEMORIES` tree are already covered by the existing `backup.backblaze`
  cron sync — nothing new to wire up there.
- No automatic scheduling (cron) in this pass. `ingest.sh` is run by hand
  until the flow is trusted.

## 2. Location & interface

`ingest.sh` lives at the Memories share root, alongside the existing
`cleanup-photos.sh` and `backup.backblaze` (i.e. `/srv/shares/memories/` on
the treasurebox host; mirrored at `/Volumes/Memories/` when tested from the
laptop over the mount). It is not part of the mop repo.

Usage is a single positional argument — the path to ingest, relative to the
script's own directory:

```
./ingest.sh NEW
```

Everything else (archive root, db paths, mop location, trash dir) is a fixed
constant at the top of the script:

```bash
ROOT="$(cd "$(dirname "$0")" && pwd)"
MOP_DIR=~/src/mop                       # wherever mop is deployed
MOP="uv run --project $MOP_DIR mopcli.py"
ARCHIVE_DB="$ROOT/.ingest/archive.db"
NEW_DB="$ROOT/.ingest/new.db"
```

`archive.db` is durable and persists across runs — it is mop's index of the
whole archive, kept current incrementally (§3, step 6), never rebuilt from
scratch. `new.db` also persists across runs so that `--skip-existing`
prevents rehashing files already seen on a prior run (in particular,
undated/flagged files — see step 5).

## 3. Flow

One run of `ingest.sh PATH` does, in order:

1. **Index the target.**
   ```
   $MOP local index --dbpath $NEW_DB --skip-existing "$PATH"
   ```
   Bulk hash via mop; `--skip-existing` means a file already indexed on a
   prior run is not rehashed. The shell script never computes a hash itself.

2. **Dedupe within the batch.** Two files new to this ingest that are
   byte-identical to each other (but not yet in the archive) should not both
   be filed as separate archive copies.

   `store dupes` is **not used here**: it joins multiple URIs per group with
   a plain space (`GROUP_CONCAT(uri, ' ')`), and this share's paths are full
   of literal spaces ("Wedding Videos", "HD Files", ...) with no
   percent-encoding — splitting that output on space is ambiguous and can
   misparse which file is which. Confirmed by direct test against a
   space-containing fixture path.

   Instead, use `store ls`, which prints one `sha1<TAB>uri` per line, sorted
   by sha1 — safe to group by adjacent lines regardless of spaces:
   ```
   $MOP store ls $NEW_DB
   ```
   Walking that output: each time the sha1 changes from the previous line,
   the uri on that line is the keeper (skip it); every subsequent line with
   the *same* sha1 as the line before it is a duplicate — collect its uri
   for trashing. Trash the collected list via
   ```
   $MOP local trash --root "$ROOT" -
   ```

3. **Dedupe against the archive.** Content already present anywhere in the
   archive gets trashed from the ingest path.
   ```
   $MOP store present $ARCHIVE_DB $NEW_DB | cut -f2 | $MOP local trash --root "$ROOT" -
   ```

4. **Find net-new content.**
   ```
   $MOP store missing $ARCHIVE_DB $NEW_DB | cut -f2
   ```
   This is the list of files whose hash exists nowhere in the archive.

5. **Place net-new files.** For each path from step 4:
   - If it no longer exists on disk (already trashed in step 2 or 3 — its
     `new.db` row is stale but harmless, see §5), skip it.
   - Run `exiftool -json` and check, in order: `DateTimeOriginal` →
     `CreateDate` → `MediaCreateDate`. First tag present wins; parse the year.
   - **No date found in any of those tags:** leave the file where it is.
     Nothing is logged separately — `--skip-existing` in step 1 already makes
     this cheap on the next run (no rehash), and it will keep appearing as
     "missing" in step 4 until it either gets a date some other way or is
     moved out by hand. That persistence *is* the flag.
   - **Date found:** `mkdir -p "$ROOT/$YEAR"` (create the year folder if this
     is the first file ever dated to that year), then move the file in. If a
     file of the same name already exists at the destination, append `-1`,
     `-2`, etc. before the extension — first free name wins. No overwrites,
     ever.

6. **Fold moved files into the durable archive index.**
   ```
   $MOP local index --dbpath $ARCHIVE_DB --skip-existing "$ROOT"
   ```
   `--skip-existing` means the ~414 GB of already-indexed archive content
   costs a directory walk and a per-file existence check, not a rehash. Only
   the files moved in step 5 (new uris under `$ROOT/$YEAR/...`) get hashed —
   and only once, ever, for their new location.

## 4. `--dry-run`

`ingest.sh` supports `--dry-run`. Steps 1 and 4 (indexing, listing) are
inherently read-only and always run for real — indexing doesn't mutate
target files, and building `new.db` is required to compute what step 5/6
*would* do. Under `--dry-run`:

- Step 2 and 3 print what would be trashed instead of trashing.
- Step 5 prints `<path> -> <would-be-destination>` (or `<path>: no date,
  would flag`) instead of moving.
- Step 6 is skipped entirely (nothing moved, so nothing new to fold in).

Without `--dry-run`, the run is fully live — there is no separate `--apply`
gate. (Unlike mop's own `dedup`/`hide` commands, `ingest.sh` is not going to
be invoked accidentally as a side effect of something else; it is always run
deliberately with a specific path argument.)

## 5. Known limitations (accepted for v1)

- **Stale `new.db` rows.** After a file is trashed (steps 2/3) or moved
  (step 5), its row in `new.db` still points to the old path. This is
  harmless: `mopcli.py local trash` already handles a since-vanished path by
  skipping it (`SKIP missing`), and once a hash is folded into `archive.db`
  (step 6), `store missing` naturally stops listing it regardless of what
  `new.db` still says about its old location. The only cost is a bit of
  repeated (cheap, hash-free) output noise on subsequent runs.
- **Filename collisions across different content.** The `-1`/`-2` suffix
  logic in step 5 also fires if an unrelated file already happens to share a
  destination filename with different content — it does not try to detect
  that case specially, it simply never overwrites.
- **No automated scheduling.** Run by hand for now (§1).

## 6. Testing

There is no test harness at the Memories share root, and this script is
deliberately outside mop's pytest suite (`tests/`) since it depends on
`exiftool`, which is not part of mop's own dependency surface. Verification
before trusting this against the real `NEW/` backlog:

1. Build a small scratch directory with: a photo with `DateTimeOriginal` set,
   a video with only `CreateDate` set, a file with no date metadata at all,
   and two byte-identical copies of one file.
2. Run `ingest.sh --dry-run` against it, confirm the printed plan matches
   expectations for all four cases.
3. Run for real, confirm: dated files land in the right `YYYY/`, the
   undated file is untouched, one of the two identical copies is trashed
   (not both moved), and `archive.db` picks up the moved files on the next
   `local index --skip-existing` without rehashing anything else.
4. Re-run `ingest.sh` a second time against the same (now-processed)
   directory and confirm it is a fast no-op: nothing rehashed except
   whatever remains (the undated file), nothing double-moved.
