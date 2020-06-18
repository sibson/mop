import binascii, hashlib, json

import click
import plyvel


class FileMetaData(object):
    def __init__(self, uri, sha1, path, **kwargs):
        self.uri = uri
        self.sha1 = sha1
        self.path = path
        self.kwargs = kwargs


    def __json__(self):
        dct = {
            'uri': self.uri,
            'path': self.path,
            'sha1': self.sha1
        }
        dct.update(self.kwargs)
        return dct


    def write(self, ldb):
        sha = self.sha1.encode()
        uris = json.loads(ldb.get(sha, b'[]').decode())
        if self.uri not in uris:
            uris.append(self.uri)

        metadata = json.dumps(self, cls=MetaDataEncoder)
        ldb.put(self.uri.encode(), metadata.encode())
        ldb.put(sha, json.dumps(uris).encode())


class MetaDataEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, FileMetaData):
            return obj.__json__()

        return json.JSONEncoder.default(self, obj)


def dumps(obj):
    return json.dumps(obj, cls=MetaDataEncoder)


def json_object_hook(dct):
    klass = dct.pop('__class__')
    if klass not in ['MetaDataEncoder']:
        return dct

    if klass == 'FileMetaData':
        return FileMetaData(dct['path'], dct['sha1'])

    return dct


def loads(data):
    return json.loads(data, object_hook=json_object_hook)


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
def ls(dbpath):
    ldb = plyvel.DB(dbpath)
    for sha, path in ldb:
        print(f'{sha.decode("UTF-8")} {path.decode("UTF-8")}')



@cli.command()
@click.argument('target')
@click.argument('source', nargs=-1)
def missing(target, sources):
    target = plyvel.DB(target)
    sources = [(s, plyvel.DB(s)) for s in sources]

    for srcname, src  in sources:
        for sha, path in src:
            if target.get(sha):
                continue

            print(f'{srcname}\t{path.decode()}')


@cli.command()
@click.argument('target')
@click.argument('source', nargs=-1)
def present(target, source):
    target = plyvel.DB(target)
    sources = [(s, plyvel.DB(s)) for s in source]

    for srcname, src  in sources:
        for sha, path in src:
            if target.get(sha):
                print(f'{srcname}\t{path.decode()}')


if __name__ == '__main__':
    cli()
