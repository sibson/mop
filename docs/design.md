# mop Design

## Purpose

mop indexes files across local filesystems and Backblaze B2, then diffs those
indexes to drive dedup/cleanup decisions. It follows the UNIX philosophy:
narrow commands, piped together, rather than one command that does everything.

## Architecture

```
mopcli.py          unified CLI entry point (click group of group)
  ├── b2.py         index/inspect/hide against a B2 bucket
  ├── local.py      index/trash/dedup against a local filesystem
  └── store.py      the index format itself: schema, connect(), FileMetaData,
                     and cross-index commands (ls, meta, missing, present,
                     merge, dupes, usage)
```

`b2.py` and `local.py` each know how to walk *their* source (bucket vs
filesystem) and produce `FileMetaData` rows. `store.py` owns everything about
what an index *is* — it has no bucket/filesystem-specific code. Each backend
writes into its own SQLite file; there is no shared/live database, and no
process holds a db open longer than one command invocation.

Commands can also be run standalone (`./local.py ...`, `./b2.py ...`,
`./store.py ...`) — `mopcli.py` just wires the three click groups together
under one binary.

## Data model

One index = one SQLite file, one `files` table, keyed by URI (the file's
location — `file:///path` or `b2://bucket/path`). `sha1` (content hash) is a
plain indexed column, not a separate maintained structure: dedup and
cross-index present/missing/merge are ordinary SQL over that index rather than
a hand-kept sha→uris mapping.

```sql
CREATE TABLE files (
    uri   TEXT PRIMARY KEY,   -- location: file:///path or b2://bucket/path
    sha1  TEXT NOT NULL,      -- content hash (dedup / cross-index key)
    path  TEXT NOT NULL,
    size  INTEGER,            -- bytes; NULL when unknown (source didn't report it)
    extra TEXT                -- JSON: remaining metadata (fileName, bucket, fileId, ...); NULL if none
);
CREATE INDEX idx_files_sha1 ON files(sha1);
```

Rationale:
- `uri` as primary key makes indexing an upsert (`INSERT OR REPLACE`) — no
  read-modify-write per file.
- `sha1` indexed, not primary, because it's not unique — many URIs share a
  hash (duplicates) by design. Grouping/filtering on it is index-driven SQL.
- `size` is a typed column (not buried in `extra`) because `usage` aggregates
  on it (`SUM`, `COUNT`, `WHERE size IS NULL`) — that only stays cheap as a
  real column.
- `extra` is open-ended JSON so each backend can carry its own metadata
  (`fileName`, `bucket`, `fileId`, ...) without schema churn or a column per
  backend-specific field.

Pragmas on connect: `journal_mode=WAL`, `synchronous=NORMAL`. Callers control
their own transactions/commits — `store.connect()` never commits for you.

Full history and the reasoning behind this schema (it replaced a LevelDB
key/value store) is in [sqlite-migration-spec.md](sqlite-migration-spec.md).

## Command surface

| Area | Commands | Role |
|------|----------|------|
| `b2.py` | `login`, `sha1sum`, `index`, `usage`, `hide` | Read/inspect a B2 bucket; `hide` is the only mutation, and only marks objects for B2 lifecycle aging (not a delete) |
| `local.py` | `sha1sum`, `index`, `trash`, `dedup` | Read/inspect a local tree; `trash`/`dedup` move files, never delete them outright |
| `store.py` | `mk`, `rm`, `truncate`, `ls`, `meta`, `missing`, `present`, `merge`, `dupes`, `usage` | Operate on index files directly: create/inspect/compare/combine, independent of where the index came from |

## Safety model

Nothing in mop deletes data outright:
- `local.py trash`/`dedup` move files into `<root>/.trash/`, preserving
  relative path, and append an entry to `trashed.log` — recoverable by `mv`
  back, auditable by reading the log.
- `dedup` re-hashes files fresh at removal time (never trusts the index alone)
  and only trashes a copy once a live survivor's hash is confirmed to match.
  A stale index row or a changed file surfaces as a mismatch and is left
  alone.
- `b2.py hide` marks an object hidden (B2's own soft-delete); it does not
  permanently remove it. `--dry-run` and `--log` make a real run inspectable
  and auditable before/after the fact.
- `store.py merge` is target-wins: a source index can only add new URIs, never
  overwrite an existing target row; a sha1 conflict on a shared URI is
  reported on stderr, not silently resolved.

## See also

- [sqlite-migration-spec.md](sqlite-migration-spec.md) — why the store is
  SQLite and not LevelDB, and the schema's design rationale in detail.
- [backblaze-cleanup-plan.md](backblaze-cleanup-plan.md) — an applied use of
  this tooling: indexing, reconciling, and safely trimming a real B2 archive.
- [mop-feature-handoff.md](mop-feature-handoff.md) — history of what mop
  needed to grow to execute that plan.
