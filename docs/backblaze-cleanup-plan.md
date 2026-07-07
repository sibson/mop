# Backblaze B2 Storage Cleanup Plan

## 0. Approach

**Cleanup operates on the local treasurebox filesystem, not directly on Backblaze B2.**

The local filesystem is the source of truth; B2 is a backup mirror. Fixing data
locally is the safe layer:

- Local changes propagate to B2 on the next sync (whatever mechanism that is —
  see Open Questions). Editing B2 directly risks being undone by a later sync, or
  discarding a backup of a good local file.
- No new mop commands are required. mop indexes and diffs; the shell acts —
  `find`/`mv` locally, the existing `b2.py hide` on B2 only if a file exists on
  B2 but not locally.
- Local removals go to a `.trash/` dir first (`mv`, not `rm`) for a recovery
  window.

---

## 1. Current State

### 1.1 Two B2 Buckets

| Bucket | Index DB (LevelDB) | Purpose |
|---|---|---|
| `sibsonmemories` | `sibsonmemories/` (7 files, ~19KB) | **Legacy.** Pre-rename sync target; frozen — nothing writes here anymore (§1.3) |
| `treasurebox-memories` | `treasurebox-memories/` (15 files, ~84KB) | Active photo/memory archive |

### 1.2 Existing Tooling

| Tool | Commands | Role in cleanup |
|---|---|---|
| `b2.py` | `login`, `sha1sum`, `index`, `usage`, `hide` | Index B2 (read), hide only if needed |
| `local.py` | `sha1sum`, `index` | Index the local treasurebox filesystem |
| `leveldb.py` | `ls`, `meta`, `present`, `missing`, `dupes`, `merge`, `usage` | Compare indexes to find candidates |
| `mopcli.py` | Wires all above | Unified entry point |

**Metadata note:** `b2.py index` records `size`; `local.py index` does **not**
(`local.py:48` stores only uri/sha1/path/fileName). So local zero-byte detection
must use the filesystem (`find -size 0`) or the empty-string SHA1 — not a `size`
field that the local index never populates.

### 1.3 Local path & sync mechanism

Confirmed by reading `~/src/treasurebox` (the ansible homelab repo that owns
this host). Full detail + the broader restic/backup findings from that repo
live in
[`~/src/treasurebox/docs/2026-07-06-memories-backup-scope-findings.md`](../../treasurebox/docs/2026-07-06-memories-backup-scope-findings.md)
— that's infra-pipeline scope, not mop's. What mop needs from it:

- **Local path:** `/srv/shares/memories` (`host_vars/treasurebox/vars:54`).
- **Sync:** plain `rclone` cron (not restic, not mop), every 6h,
  local → `backblaze:Treasurebox-Memories`. Excludes `.DS_Store` etc. going
  forward but has no `--delete-excluded`, so old excluded objects already in
  the bucket aren't retroactively cleaned — that's why `.DS_Store` still needs
  a manual B2-side sweep here (Finding D).
- **`sibsonmemories` is dead** — pre-rename target, nothing writes to it
  anymore. See Finding E.
- **Bucket casing:** live target is `Treasurebox-Memories` (mixed case) — don't
  assume it's string-identical to the lowercase `treasurebox-memories` used
  for local DB/dir naming in this repo. Confirm the exact name before running
  `b2.py` against it.

---

## 2. Findings and Supporting Reasoning

### Finding A: The B2 dump (`list.memories`) is contradictory — verify against local, don't trust it

**Evidence:** `list.memories` (a 2023-02-18 `b2.py index` output) lists 125 files.
124 print `0.0Mb` with SHA1 `da39a3ee5e6b4b0d3255bfef95601890afd80709` — the SHA1
of an empty byte string. The 125th is `IMG_20130613_193657-MOTION.gif`, 1.8MB.
**But the dump's own total line reads `Total 125 files, 1846.3916MB`** — which 124
zero-byte files plus one 1.8MB file cannot produce.

**Reasoning:** An empty-string SHA1 proves the indexer *hashed zero bytes on the
B2 side*. It does **not** prove the local file is empty — it can equally be a
failed/truncated upload or a partial index run (the 1846MB total suggests
something is off). This stale, self-contradictory dump is not evidence for
deleting anything. The real test is Phase 1: index the local filesystem, where
SHA1 is computed from actual bytes. Act on *that*.

Decision for the 124 suspect files:

| Local file | Action |
|---|---|
| real content | keep locally; the broken B2 copy self-heals on next sync (re-upload) — do **not** hide |
| 0 bytes | trash locally; hide the B2 mirror if it lingers |
| absent | decide authority (Open Q) before touching |

### Finding B: Files duplicated under two path prefixes (`NEW/` vs `NEW/new/`)

**Evidence:** `list.memories` shows files under both `NEW/2012/...` and
`NEW/new/2012/...`, currently sharing the (untrustworthy) empty SHA1.

**Reasoning:** Looks like a sync bug or manual reorganize leaving the old prefix
in place. Until Phase 1 gives real local hashes we can't confirm the copies are
identical. Resolve after local content hashes exist (`leveldb.py dupes`).

### Finding C: No audit trail / trash workflow

**Evidence:** `hidden.files` is 0 bytes, never populated. No staging step exists
before a removal.

**Reasoning:** `rm` (local) and `hide` (B2) are unrecoverable. Working locally
makes recovery cheap: `mv file .trash/` instead of `rm`. `ls -R .trash` is the
audit trail.

### Finding D: `.DS_Store` clutter

**Evidence:** README documents a B2 `.DS_Store` hide pipeline. They likely exist
locally too.

**Reasoning:** macOS directory metadata, no value in the archive. Remove locally;
sync propagates.

### Finding E: `sibsonmemories` is a frozen legacy bucket — safe to prune, but only after a full coverage check

**Evidence:** §1.3 — `sibsonmemories` was the sync target before a rename to
`Treasurebox-Memories`; the old invocation is dead-commented in
`files/backup.backblaze:9`. No cron, no process, nothing on the treasurebox host
still targets it.

**Reasoning:** Being frozen means it's safe to fully retire *content-wise* — but
"frozen" is not the same as "fully superseded." The rename could have happened
before every historical file was carried over, and the two buckets were indexed
years apart (`sibsonmemories/` ~19KB vs `treasurebox-memories/` ~84KB — different
sizes, consistent with different points in time, not necessarily identical
content). **Do not hide anything in `sibsonmemories` on the assumption that it's
"probably" duplicated.** Verify every object's SHA1 exists in
`treasurebox-memories` first — this is a straight `leveldb.py missing` run, no
new tooling needed (Phase 2e below). Only what's confirmed present in the active
bucket is safe to hide; anything left over needs a decision (Open Q6) before
touching it.

---

## 3. Cleanup Plan

Order: **establish local truth → find junk → trash locally → reconcile B2.**

### Phase 0: Locate the local treasurebox

```bash
export TREASUREBOX_PATH=/srv/shares/memories   # confirmed: host_vars/treasurebox/vars:54, ~/src/treasurebox
```

(Was an open question; answered in §1.3 by reading the ansible repo.)

---

### Phase 1: Index the local filesystem

```bash
./local.py index --dbpath treasurebox-local.db $TREASUREBOX_PATH

# refresh the B2 index too, for reconciliation (replaces stale list.memories)
./b2.py index --recursive --dbpath treasurebox-b2.db treasurebox-memories
```

**Output:** `treasurebox-local.db` (trustworthy, byte-computed SHA1s) and a fresh
`treasurebox-b2.db`.

---

### Phase 2: Identify candidates

#### 2a. Zero-byte files — filesystem, not the index

`local.py index` has no `size` field, so filter on the filesystem directly:

```bash
find $TREASUREBOX_PATH -type f -size 0 > zero-byte-local.txt
```

(Equivalently, empty files carry SHA1 `da39a3ee…` in `treasurebox-local.db`.)

#### 2b. `.DS_Store` files

```bash
find $TREASUREBOX_PATH -type f -name .DS_Store > dsstore-local.txt
```

#### 2c. Reconcile local ↔ B2 (content, by SHA1)

```bash
# local files NOT on B2 (or on B2 with a different hash) → un-synced / re-upload
./leveldb.py missing treasurebox-b2.db treasurebox-local.db

# B2 objects with no matching local file → B2-only, candidates to hide
./leveldb.py missing treasurebox-local.db treasurebox-b2.db
```

#### 2d. Intra-tree duplicates (real hashes now)

```bash
./leveldb.py dupes treasurebox-local.db
```

Use this to confirm/resolve Finding B's `NEW/` vs `NEW/new/` mirroring.

#### 2e. `sibsonmemories` coverage check (Finding E) — prerequisite for pruning the legacy bucket

Index the legacy bucket, then check every object's SHA1 lands in the active
bucket. This is the superset test: **empty output = fully covered, safe to hide
all of `sibsonmemories`.** Any line printed = a file that only exists in the
legacy bucket — do **not** hide those until reconciled (re-upload to
`Treasurebox-Memories`, or explicit decision to let them go).

```bash
./b2.py index --recursive --dbpath sibsonmemories-b2.db sibsonmemories

# sibsonmemories objects whose SHA1 is NOT present in treasurebox-b2.db:
./leveldb.py missing treasurebox-b2.db sibsonmemories-b2.db > sibsonmemories-not-covered.txt

wc -l sibsonmemories-not-covered.txt   # 0 → fully superseded, proceed to Phase 4b
```

Note this is a **content** (SHA1) match, not a path match — a legacy file that
was renamed/moved under the new prefix still counts as covered.

---

### Phase 3: Trash candidates locally

Never `rm`. Move to `.trash/`, preserving relative paths:

```bash
mkdir -p $TREASUREBOX_PATH/.trash

trash() {   # usage: trash <absolute-path>
  rel="${1#$TREASUREBOX_PATH/}"
  mkdir -p "$(dirname "$TREASUREBOX_PATH/.trash/$rel")"
  mv "$1" "$TREASUREBOX_PATH/.trash/$rel"
}
```

#### 3a. Zero-byte files

```bash
while read -r p; do trash "$p"; done < zero-byte-local.txt
```

#### 3b. `.DS_Store`

```bash
while read -r p; do trash "$p"; done < dsstore-local.txt
```

#### 3c. Duplicate prefix (decision needed)

Pick the canonical prefix, then `trash` each file under the other one.

---

### Phase 4: Reconcile B2 (only if not auto-synced)

If a sync mechanism mirrors local→B2, do nothing here — trashing locally
propagates on the next run. Verify the sync in 5c.

If there is **no** sync, or for B2-only files with no local counterpart, use the
existing `hide`:

```bash
# always eyeball the list first
./leveldb.py missing treasurebox-local.db treasurebox-b2.db | cut -f2 > b2-only.txt

# hide B2-only objects (mark for lifecycle aging, not permanent delete)
cat b2-only.txt | ./b2.py hide treasurebox-memories -
```

---

### Phase 4b: Retire `sibsonmemories` (only if Phase 2e's coverage check came back empty)

```bash
# guard: re-check before acting, don't rely on a stale run
test ! -s sibsonmemories-not-covered.txt && echo "OK: fully superseded, safe to hide all"

./leveldb.py meta sibsonmemories-b2.db | jq -r '.uri' | ./b2.py hide --dry-run sibsonmemories -
# review, then:
./leveldb.py meta sibsonmemories-b2.db | jq -r '.uri' | ./b2.py hide --log hidden.log sibsonmemories -
```

If Phase 2e found uncovered files, hide only the covered ones (invert the
`missing` result — or just wait; a partial legacy bucket is low-cost to leave
alone until the leftovers are resolved).

---

### Phase 5: Verify

```bash
# 5a. re-index local, check totals
./local.py index --dbpath treasurebox-local.db $TREASUREBOX_PATH

# 5b. confirm trash contents
find $TREASUREBOX_PATH/.trash -type f | wc -l
du -sh $TREASUREBOX_PATH/.trash

# 5c. confirm sync ran (whatever the mechanism is) and B2 reflects the cleanup
./b2.py index --recursive --dbpath treasurebox-b2.db treasurebox-memories
./b2.py usage --top 20 treasurebox-memories
```

---

### Phase 6: Long-Term (optional, no new mop commands required)

1. Cron to purge trash after N days: `find $TREASUREBOX_PATH/.trash -mtime +30 -delete`
2. A shell alias/script wrapping the Phase 2/3 `find`+`trash` sweep — kept outside
   mop, since it's filesystem hygiene, not indexing.
3. Watch for new zero-byte files reappearing → signals an upstream upload bug.

---

## 4. Summary Table

| Phase | Action | Risk | Requires Decision? | New Tooling? |
|---|---|---|---|---|
| 0 | Locate `$TREASUREBOX_PATH` | None | **Yes** | No |
| 1 | Index local (+ refresh B2) | None | No | No |
| 2a | Find zero-byte (`find -size 0`) | None | No | No |
| 2b | Find `.DS_Store` | None | No | No |
| 2c | Reconcile local↔B2 | None | No | No |
| 2d | Local dupes | None | No | No |
| 3a | Trash zero-byte | Low | No | No |
| 3b | Trash `.DS_Store` | Low | No | No |
| 2e | `sibsonmemories` coverage check | None (read) | No | No |
| 3c | Trash non-canonical prefix | Med | **Yes** — which prefix? | No |
| 4 | Reconcile B2 (hide B2-only) | Med | **Yes** — synced or hide? | No |
| 4b | Retire `sibsonmemories` | Low, gated on 2e | No, if 2e is clean | No |
| 5 | Re-index + verify | None | No | No |
| 6 | Long-term hygiene | Low | **Yes** — trash policy | No |

---

## 5. Open Questions

~~1. What is `$TREASUREBOX_PATH`?~~ **Answered:** `/srv/shares/memories` (§1.3).
~~3. What syncs local→B2?~~ **Answered:** rclone cron, `files/backup.backblaze` in
`~/src/treasurebox` (§1.3).
~~5. What is `sibsonmemories` relative to `treasurebox-memories`?~~ **Answered:**
legacy, frozen, pre-rename (§1.3, Finding E) — but "frozen" ≠ "fully covered";
Phase 2e verifies that before anything is hidden.

Remaining:

1. For the 124 suspect files: do the **local** copies have real content? (Phase 1
   answers this — determines re-upload/keep vs trash.)
2. Which prefix (`NEW/` or `NEW/new/`) is canonical?
3. How long should `.trash` be retained before auto-purge?
4. Does Phase 2e's coverage check come back clean, or are there pre-rename files
   in `sibsonmemories` that never made it into `Treasurebox-Memories`?
5. Confirm the exact live bucket name/casing (`Treasurebox-Memories` vs
   `treasurebox-memories`) before running any `b2.py` command against it.
