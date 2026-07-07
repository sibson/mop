# Spec: Migrate index store from LevelDB (plyvel) to native SQLite

Status: draft
Owner: mop
Related: replaces the `plyvel` / Homebrew-leveldb toolchain

## 1. Goal

Replace the LevelDB backing store with a **native SQLite** design — real
tables and SQL, no key/value emulation, no plyvel-shaped shim.

Delete the whole fragile C-linkage problem: plyvel links the system leveldb,
Homebrew builds leveldb `-fno-rtti`, so plyvel must be force-recompiled with a
matching flag or `import plyvel` dies at runtime on a missing
`leveldb::Comparator` typeinfo symbol (see `Makefile` `plyvel` target, README).

SQLite ships inside CPython: zero third-party dep, zero compile, works on every
macOS/Linux/Windows Python ≥3.11.

### Non-goals
- No change to CLI command names, arguments, or human-readable output. The perf
  harness and existing test expectations depend on output stability.
- No new query features. Pure backing-store swap.
- No concurrency/multi-writer support (single-process CLI, same as today).

## 2. Current data model (what we're replacing)

Two plyvel `prefixed_db` namespaces inside one leveldb dir:

| Namespace | Key (bytes) | Value (bytes) |
|-----------|-------------|---------------|
| `meta`    | `uri`       | JSON blob `{uri, path, sha1, size?, fileName?, bucket?, fileId?, ...}` |
| `sha`     | `sha1`      | JSON list of uris sharing that sha1 |

Write path (`FileMetaData.write`, `leveldb.py:60`): for every file — `get` the
sha list, append uri, `put` meta, `put` sha list. 1 read + 2 writes per file,
unbatched, and the sha→uri list is maintained by hand.

Access patterns in use (full inventory):
- point put (index), point get (`--skip-existing`, `missing`/`present` target)
- full-namespace scan (`ls`, `meta`, `dupes`, `usage`, `merge`)
- multi-db scan + point get across dbs (`missing`, `present`, `merge`)

No range queries. No dependence on key sort order for correctness.

## 3. New SQLite schema

One table. The sha→uris relationship is **derived**, not stored.

```sql
CREATE TABLE files (
    uri   TEXT PRIMARY KEY,   -- was the `meta` key
    sha1  TEXT NOT NULL,      -- promoted column (was `sha` namespace key)
    path  TEXT NOT NULL,
    size  INTEGER,            -- promoted: usage/audit filters & sums on it; NULL allowed
    extra TEXT                -- JSON: remaining kwargs (fileName, bucket, fileId, mtime, ...)
);
CREATE INDEX idx_files_sha1 ON files(sha1);
```

Rationale:
- `uri` PRIMARY KEY → point get/put become a single upsert; the manual sha-list
  read-modify-write disappears.
- `sha1` indexed column → dupes/missing/present/merge are index-driven SQL, and
  the uri-list-per-sha is reconstructed by `GROUP BY sha1` / `WHERE sha1=?`.
- `size` promoted to a typed column → `usage` aggregates in SQL (`SUM`, `COUNT`,
  `WHERE size IS NULL`) instead of scanning + JSON-decoding every row in Python.
- `extra` keeps the open-ended kwargs behavior of `FileMetaData` (forward-compat
  for arbitrary metadata) without schema churn.

Pragmas on open: `journal_mode=WAL`, `synchronous=NORMAL`. Index writes wrapped
in a single transaction per `index` run (commit once at end, or every K rows).

On-disk artifact changes from a **directory** (leveldb) to a **single file**
(`*.db`, plus transient `-wal`/`-shm`). `rm`/`truncate` must handle the file +
sidecars, not a dir tree.

## 4. Command reimplementation map

All in `leveldb.py` (module kept for CLI compat; rename to `store.py` optional,
see §6). `import plyvel` removed everywhere; `import sqlite3`.

| Command | New implementation |
|---------|--------------------|
| `mk`       | connect + `CREATE TABLE`; error if file exists |
| `rm`       | `os.remove` db + `-wal`/`-shm` if present |
| `truncate` | `DELETE FROM files` (or drop file + recreate) |
| `ls [wc]`  | `SELECT sha1, uri FROM files ORDER BY sha1`; apply `fnmatch` (or SQL `GLOB`) to uri; print `sha\turi` |
| `meta`     | `SELECT uri,path,sha1,size,extra FROM files`; re-emit JSON identical to old `meta` value |
| `missing target src...` | `ATTACH` each src; `SELECT DISTINCT sha1,uri FROM src.files WHERE sha1 NOT IN (SELECT sha1 FROM main.files)` |
| `present target src...` | same, `WHERE sha1 IN (...)` |
| `merge target src...`   | `ATTACH` src; `INSERT OR IGNORE INTO main.files SELECT * FROM src.files` (dedup on uri PK) |
| `dupes`    | `SELECT sha1, GROUP_CONCAT(uri,' ') FROM files GROUP BY sha1 HAVING COUNT(*)>1` |
| `usage`    | SQL aggregate for totals/`size IS NULL` count; folder/ext breakdown may stay in Python over a single scan |

`FileMetaData` stays as the in-memory record + JSON shape. `.write(conn)` becomes
one `INSERT OR REPLACE INTO files(uri,sha1,path,size,extra) VALUES(...)`, where
`size` is lifted out of kwargs and the rest serialized to `extra`.

Callers `local.py:40` and `b2.py:60` swap `plyvel.DB(...)` for the sqlite open
helper; `b2.py` "wipe then rebuild" becomes drop-file + `CREATE`. `index` wraps
the walk in one transaction.

`meta`/`ls` output must be byte-for-byte what the old code produced (tests in
`tests/test_leveldb.py` assert exact strings) — reconstruct the JSON blob with
the same key set, including omitting absent optionals.

## 5. Existing-data migration (one-time)

`treasurebox-b2.db`, `treasurebox-local.db`, `treasurebox-memories/` are live
leveldb dirs. One-shot converter, run **while plyvel still installed**, before
dropping the dep:

`migrate_leveldb_to_sqlite.py OLD_LEVELDB_DIR NEW.db`
- open old with plyvel, scan `meta` namespace, `FileMetaData.construct` each,
  write into the new sqlite `files` table (sha derivation is automatic).
- verify row count == old meta count and that `dupes`/`usage` outputs match
  between old and new before deleting the leveldb dir.

Sequence: migrate all dbs → verify → remove plyvel from `pyproject.toml`/`uv.lock`
→ delete `Makefile` plyvel target + README brew/`-fno-rtti` notes.

## 6. File / dependency changes

- `pyproject.toml`: drop `plyvel` dependency. (`sqlite3` is stdlib.)
- `Makefile`: delete `plyvel`/`setup` leveldb targets; `setup` becomes `uv sync`.
- `README.md`: delete brew leveldb + `-fno-rtti` sections.
- `leveldb.py`: rewrite on sqlite. Keep filename+CLI group name for compat, or
  rename module to `store.py` and update imports in `mopcli.py`, `local.py`,
  `b2.py`, tests (decide during impl; rename is cosmetic, do it or don't in one
  commit).
- `tests/conftest.py`: remove the `sys.modules['plyvel']` MagicMock; tests now
  run against a real sqlite temp-file db (simpler, higher fidelity).
- `tests/test_leveldb.py`: replace mock-call assertions with real-db round-trips
  (write rows, invoke CLI, assert output). Keep the same output assertions.

## 7. Performance test spec

Purpose: quantify the store swap. Run the **same harness** on leveldb `main` and
the sqlite branch; compare. Decision metric is write throughput and read latency
at scale; regression guard is end-to-end `index` and on-disk size.

### 7.1 Portability strategy

The harness drives the **CLI via `subprocess`** (`uv run mopcli.py ...`). Command
names/args are unchanged by the migration, so one harness file runs unmodified on
both backends — no import coupling to plyvel or sqlite internals.

Run leveldb baseline from a `git worktree` of `main` (with plyvel built) and the
sqlite result from the branch. Harness writes a JSON result file per backend; a
compare step diffs them.

### 7.2 Fixed synthetic corpus (generated once, outside all timing)

- `N` logical files (default 100_000; also report at 10k for a fast loop).
- Deterministic, seeded generator (fixed seed — reproducible across backends).
- Duplicate ratio: `DUP=0.1` → 10% of entries reuse an existing sha1 (exercises
  the sha-list path / `GROUP BY`); the rest unique.
- Path shape: `top{0..49}/sub{0..99}/file{i}.{ext}` with ext drawn from a fixed
  small set (`jpg,mp4,txt,pdf,none`) so `usage` folder/ext breakdown has spread.
- `size`: pseudo-random 1KB–4GB; ~2% rows have `size=NULL` (exercises the
  `usage` "without size data" branch).

Two corpus forms:
- **Records list** (in-memory list of `(uri,sha1,path,size,extra)`) for the
  write microbench — no disk I/O, no hashing in the timed region.
- **Real tiny-file tree** of `K` files (default 5_000, each 4KB random content)
  for the end-to-end `local index` macro run.

### 7.3 Metrics

| # | Phase | Input | Metric | Why |
|---|-------|-------|--------|-----|
| 1 | Store write | N records | files/sec, wall-clock | primary: replaces 1get+2put/file, unbatched |
| 2 | DB size | populated db | bytes on disk (db + wal after checkpoint) | storage regression guard |
| 3 | `ls` (full scan) | N | wall-clock, ms | read path |
| 4 | `meta` (full scan) | N | wall-clock | read path + JSON re-emit cost |
| 5 | `dupes` | N, DUP=0.1 | wall-clock | GROUP BY vs manual list scan |
| 6 | `usage` | N | wall-clock | SQL aggregate vs Python scan+JSON |
| 7 | `missing`/`present` | target N vs 1 src N/2 | wall-clock | cross-db (ATTACH vs multi-open) |
| 8 | `merge` | target N + src N/2 | wall-clock, result rows | cross-db write |
| 9 | e2e `local index` | K real files | files/sec | regression guard incl. hashing (backend-independent term) |

Write throughput (#1) is measured two ways and both reported:
- **microbench**: loop the N records straight through the writer
  (`FileMetaData.write`), transaction-wrapped — isolates pure store cost.
- **e2e** (#9): full `local index` on the real tree — user-visible, but sha1
  hashing dominates and is backend-independent; used only to prove no regression.

### 7.4 Method / controls

- Warm cache: 1 untimed warmup run, then report **median of 3**.
- Fresh db per write measurement (delete artifact before timing).
- Close/checkpoint the db between write and read phases so measurements aren't
  reading dirty WAL pages.
- Same machine, note CPU/disk (SSD) and Python version in the result file.
- Record: backend, N, DUP, K, per-phase median + min/max, db size, timestamp,
  git SHA. Timestamp/SHA captured by the shell wrapper, not inside timed code.
- Fair-write note: leveldb baseline is unbatched by design (that's today's
  behavior); the sqlite target is transaction-batched. This is a real product
  delta, not a rigging — report both the batched number and a non-batched
  sqlite number for apples-to-apples store overhead.

### 7.5 Deliverables

- `bench.py` (subprocess CLI driver, `--n --dup --k --backend-label --out`).
- `bench/results/leveldb.json`, `bench/results/sqlite.json`.
- A `make bench` target and a short compare printout (per-phase delta %,
  ✅/⚠️ regression flag).

## 8. Acceptance criteria

1. `import plyvel` gone from the codebase; `plyvel` removed from deps/lock.
2. `make setup` == `uv sync`; no brew/`-fno-rtti` steps; fresh clone works on a
   machine with no `leveldb` installed.
3. All existing tests pass, rewritten against real sqlite, with **identical**
   CLI output assertions (`ls`, `meta`, `dupes`, `usage`, CSV).
4. Existing `treasurebox-*.db` data migrated; `dupes`/`usage` output matches
   pre-migration.
5. Perf: `bench.py` runs on both backends; write throughput and read latencies
   reported. Target: no read regression >10%; write throughput ≥ leveldb
   (expected win from batching + single upsert). Any regression documented.

## 9. Rollout

1. Land `bench.py` first (works via CLI subprocess). Capture **leveldb baseline**
   from `main` → `bench/results/leveldb.json`.
2. Branch: rewrite `leveldb.py` on sqlite + update `local.py`/`b2.py`.
3. Rewrite tests against real sqlite; green.
4. Run `bench.py` on branch → `sqlite.json`; compare, record numbers in PR.
5. Run migration script on the real `treasurebox-*.db`; verify.
6. Strip plyvel from deps/Makefile/README.
7. PR with before/after perf table.
