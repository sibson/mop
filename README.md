# mop
Collection of CLI commands to help de-dupe, merge and manage files between local and remote systems. Mop follows the UNIX philosophy of combining commands together to build pipelines


# Usage
Mop uses index files to drive most operations.

## Index Files
It indexers for different collections of files

### Backblaze
```
./b2.py index --recursive --dbpath mybucket.db MyBucket
```
### Local filesystem
```
./local.py index --dbpath somedir.db /home/myuser/somedir
```


### Backblaze: Hide files matching a RegEx

```
./leveldb.py meta b2.mybucket | jq -r 'select(.path | test(".\\.DS_Store")) | .path' | ./b2.py hide MyBucket -
```

### List files that are present in targetindex and source indexes 

./leveldb.py present targetindex someindex1 someindex2


### Hide files in a Bucket if they exist in another bucket

/leveldb.py present treasurebox-memories sibsonmemories | cut -f 2 | ./b2.py hide sibsonmemories -
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
