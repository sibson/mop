# mop
Collection of CLI commands to help de-dupe, merge and manage files between local and remote systems. Mop follows the UNIX philosophy of combining commands together to build pipelines


# Setup

Managed with [uv](https://docs.astral.sh/uv/). The index store is native SQLite
(Python stdlib) — no system libraries to build:

```
make setup                     # uv sync
```

Run commands via `uv run`:

```
uv run mopcli.py --help
```

Or use individual scripts:
- `uv run b2.py` — Backblaze B2 operations
- `uv run local.py` — Local filesystem operations
- `uv run store.py` — Index database analysis


## Indexing

### Backblaze
```
./b2.py index --recursive --dbpath mybucket.db MyBucket
```
### Local filesystem
```
./local.py index --dbpath somedir.db /home/myuser/somedir
```


## Storage Auditing

### Live bucket usage (no database needed)
```
./b2.py usage --top 20 MyBucket
```

### From an indexed database (requires `size` in metadata)
```
./store.py usage --top 20 --csv mybucket.db
```


## Cross-Index Operations

### List files present in targetindex AND source indexes
```
./store.py present targetindex someindex1 someindex2
```

### List files missing from targetindex but present in sources
```
./store.py missing targetindex someindex1 someindex2
```

### Find duplicates within a single index
```
./store.py dupes mybucket.db
```

### Merge indexes
```
./store.py merge targetindex sourceindex1 sourceindex2
```


## Action: Hide/Delete Files

### Hide files matching a RegEx
```
./store.py meta b2.mybucket | jq -r 'select(.path | test(".\\.DS_Store")) | .path' | ./b2.py hide MyBucket -
```

### Hide files in a Bucket if they exist in another bucket
```
./store.py present treasurebox-memories sibsonmemories | cut -f 2 | ./b2.py hide sibsonmemories -
```


# Development

## DB Format

A single SQLite file. One row per indexed file, keyed by URI (its location);
`sha1` (the content hash) is an indexed column, so duplicate detection and
cross-index present/missing are derived by query rather than a separately
maintained index.

```sql
CREATE TABLE files (
    uri   TEXT PRIMARY KEY,   -- location, e.g. b2://bucket/path or file:///path
    sha1  TEXT NOT NULL,      -- content hash (dedup / cross-index key)
    path  TEXT NOT NULL,
    size  INTEGER,            -- bytes; NULL when unknown
    extra TEXT                -- JSON: fileName, bucket, fileId, ... (NULL if none)
);
CREATE INDEX idx_files_sha1 ON files(sha1);
```

`merge` is target-wins: a source never overwrites an existing target URI. When a
source disagrees on a URI's `sha1`, the target value is kept and the conflict is
reported on stderr.

## Metadata

### b2
    URI: b2://bucket/path
    sha1:
    fileName:
    fileId:
    size:    (bytes, since mop b2 index fix)

### Local
    URI: file:///path
    sha1:
    fileName:

## Testing

```
make test          # or: uv run pytest tests/ -v
```
