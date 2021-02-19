# mop
Collection of CLI commands to help de-dupe, merge and manage files between local and remote systems


## Usage

### Index Files
Create a DB that indexes file metadata
```
./b2.py index --dbpath b2.db MyBucket
./local.py index --dbpath somedir.db /home/myuser/somedir
```

### Backblaze: Hide files matching a RegEx

```
./b2.py index --recursive MyBucket --dbpath b2.mybucket
./leveldb.py meta b2.mybucket | jq -r 'select(.path | test(".\\.DS_Store")) | .path' | ./b2.py hide MyBucket -
```
### Find duplicates

Duplicates within a single tree
```
```

Duplicates across multiple locations

```
```


# Development


## DB Format

sha'SHA': [URI]
meta'URI': {METADATA}


## Metadata

### b2
    URI: b2://bucket/path
    sha1: 
    fileName:
    fileId:

### Local
    URI: file:///path
    sha1:
    fileName:
