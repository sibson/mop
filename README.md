# mop
Collection of CLI commands to help de-dupe, merge and manage files between local and remote systems. Mop follows the UNIX philosophy of combining commands together to build pipelines


# Quick Start

```
./mopcli.py --help
```

Or use individual scripts:
- `./b2.py` — Backblaze B2 operations
- `./local.py` — Local filesystem operations
- `./leveldb.py` — Index database analysis


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
./leveldb.py usage --top 20 --csv mybucket.db
```


## Cross-Index Operations

### List files present in targetindex AND source indexes
```
./leveldb.py present targetindex someindex1 someindex2
```

### List files missing from targetindex but present in sources
```
./leveldb.py missing targetindex someindex1 someindex2
```

### Find duplicates within a single index
```
./leveldb.py dupes mybucket.db
```

### Merge indexes
```
./leveldb.py merge targetindex sourceindex1 sourceindex2
```


## Action: Hide/Delete Files

### Hide files matching a RegEx
```
./leveldb.py meta b2.mybucket | jq -r 'select(.path | test(".\\.DS_Store")) | .path' | ./b2.py hide MyBucket -
```

### Hide files in a Bucket if they exist in another bucket
```
./leveldb.py present treasurebox-memories sibsonmemories | cut -f 2 | ./b2.py hide sibsonmemories -
```


# Development

## DB Format

```
sha'SHA': [URI]
meta'URI': {METADATA}
```

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
python3 -m pytest tests/ -v
```
