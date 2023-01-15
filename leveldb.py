#!/usr/bin/env python3
import binascii, hashlib, json, fnmatch

import click
import plyvel

class MetaDataEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, FileMetaData):
            return obj.__json__()

        return json.JSONEncoder.default(self, obj)


def dumps(obj):
    return json.dumps(obj, cls=MetaDataEncoder)


def loads(s):
    return json.loads(s)



class FileMetaData(object):
    def __init__(self, uri, sha1, path, **kwargs):
        self.uri = uri
        self.sha1 = sha1
        self.path = path
        self.kwkeys = []
        for k in kwargs:
            if '__' in k:  # skip __class__ from old metadata
                continue
            setattr(self, k, kwargs[k])
            self.kwkeys.append(k)

    def __str__(self):
        return f'<FileMetaData {self.uri}>'

    def __repr__(self):
        return f'<FileMetaData {self.uri}>'

    def __json__(self):
        dct = {
            'uri': self.uri,
            'path': self.path,
            'sha1': self.sha1
        }
        for key in self.kwkeys:
            dct[key] = getattr(self, key)

        return dct

    @staticmethod
    def construct(data):
        uri = data.pop('uri')
        sha1 = data.pop('sha1')
        path = data.pop('path')
        return FileMetaData(uri, sha1, path, **data)

    def write(self, ldb):
        sha = self.sha1.encode()
        uris = loads(ldb.get(sha, b'[]').decode())
        if self.uri not in uris:
            uris.append(self.uri)

        metadata = dumps(self)
        ldb.prefixed_db(b'meta').put(self.uri.encode(), metadata.encode())
        ldb.prefixed_db(b'sha').put(sha, dumps(uris).encode())



@click.group()
def cli():
    pass


@cli.command()
@click.argument('dbpath')
def mk(dbpath):
    ldb = plyvel.DB(dbpath, create_if_missing=True, error_if_exists=True)


@cli.command()
@click.argument('dbpath')
def rm(dbpath):
    plyvel.destroy_db(dbpath)


@cli.command()
@click.argument('dbpath')
def truncate(dbpath):
    plyvel.destroy_db(dbpath)
    ldb = plyvel.DB(dbpath, create_if_missing=True, error_if_exists=True)

@cli.command()
@click.argument('dbpath')
@click.argument('wildcard', default=None)
def ls(dbpath, wildcard):
    ldb = plyvel.DB(dbpath)
    for sha, uris in ldb.prefixed_db(b'sha'):
        uris = loads(uris)
        if wildcard:
            uris = (u for u in uris if fnmatch.fnmatch(u, wildcard))
        for uri in uris:
            print(f'{sha.decode("UTF-8")}\t{uri}')


@cli.command()
@click.argument('dbpath')
def meta(dbpath):
    ldb = plyvel.DB(dbpath)
    for uri, meta in ldb.prefixed_db(b'meta'):
        print(meta.decode())


@cli.command()
@click.argument('target')
@click.argument('source', nargs=-1)
def missing(target, sources):
    target = plyvel.DB(target).prefixed_db('sha')
    sources = [(s, plyvel.DB(s).prefixed_db('sha')) for s in sources]

    for srcname, src  in sources:
        for sha, uris in src:
            if target.get(sha):
                continue

            for uri in loads(uris):
                print(f'{srcname}\t{uri}')


@cli.command()
@click.argument('target')
@click.argument('source', nargs=-1)
def present(target, source):
    target = plyvel.DB(target).prefixed_db(b'sha')
    sources = [(s, plyvel.DB(s).prefixed_db(b'sha')) for s in source]

    for srcname, src  in sources:
        for sha, uris in src:
            if target.get(sha):
                for uri in loads(uris):
                    print(f'{srcname}\t{uri}')


@cli.command()
@click.argument('target')
@click.argument('source', nargs=-1)
def merge(target, source):
    target = plyvel.DB(target)
    sources = [(s, plyvel.DB(s)) for s in source]

    for srcname, src  in sources:
        metadata = (FileMetaData.construct(loads(j)) for k, j in src.prefixed_db(b'meta'))
        for md in metadata:
            click.echo(f'{md.uri}')
            md.write(target)


@cli.command()
@click.argument('dbpath')
def dupes(dbpath):
    db = plyvel.DB(dbpath).prefixed_db(b'sha')
    for sha, uris in db:
        uris = loads(uris)
        print (sha)
        if len(uris) > 1:
            print(' '.join(uris))


if __name__ == '__main__':
    cli()
