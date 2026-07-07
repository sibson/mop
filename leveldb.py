#!/usr/bin/env python3
import binascii, hashlib, json, fnmatch

import click


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
        uris = loads(ldb.prefixed_db(b'sha').get(sha, b'[]').decode())
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
    import plyvel
    ldb = plyvel.DB(dbpath, create_if_missing=True, error_if_exists=True)


@cli.command()
@click.argument('dbpath')
def rm(dbpath):
    import plyvel
    plyvel.destroy_db(dbpath)


@cli.command()
@click.argument('dbpath')
def truncate(dbpath):
    import plyvel
    plyvel.destroy_db(dbpath)
    ldb = plyvel.DB(dbpath, create_if_missing=True, error_if_exists=True)


@cli.command()
@click.argument('dbpath')
@click.argument('wildcard', default=None)
def ls(dbpath, wildcard):
    import plyvel
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
    import plyvel
    ldb = plyvel.DB(dbpath)
    for uri, meta in ldb.prefixed_db(b'meta'):
        print(meta.decode())


@cli.command()
@click.argument('target')
@click.argument('source', nargs=-1)
def missing(target, sources):
    import plyvel
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
    import plyvel
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
    import plyvel
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
    import plyvel
    db = plyvel.DB(dbpath).prefixed_db(b'sha')
    for sha, uris in db:
        uris = loads(uris)
        if len(uris) > 1:
            print(f'{sha.decode()}\t{" ".join(uris)}')


@cli.command()
@click.argument('dbpath')
@click.option('--top', default=10, help='Number of top folders/files to show')
@click.option('--csv', is_flag=True, help='Output as CSV')
def usage(dbpath, top, csv):
    """Analyze storage usage from an indexed database."""
    import plyvel
    db = plyvel.DB(dbpath)
    folders = {}
    extensions = {}
    largest = []
    total_files = 0
    total_size = 0
    size_not_tracked = 0

    for key, meta in db.prefixed_db(b'meta'):
        data = loads(meta.decode())
        path = data.get('path', '')
        size = data.get('size')
        if size is not None:
            total_files += 1
            total_size += size
            folder = path.split('/')[0] if '/' in path else path
            folders[folder] = folders.get(folder, 0) + size
            ext = path.rsplit('.', 1)[-1] if '.' in path else '(none)'
            extensions[ext] = extensions.get(ext, 0) + size
            largest.append((size, path))
        else:
            size_not_tracked += 1

    largest.sort(key=lambda x: -x[0])
    largest = largest[:top]

    if csv:
        click.echo('category,name,size_bytes')
        click.echo(f'summary,total_files,{total_files}')
        click.echo(f'summary,total_size,{total_size}')
        if size_not_tracked:
            click.echo(f'summary,size_not_tracked,{size_not_tracked}')
        for folder, size in sorted(folders.items(), key=lambda x: -x[1]):
            click.echo(f'folder,{folder},{size}')
        for size, path in largest:
            click.echo(f'file,{path},{size}')
        for ext, size in sorted(extensions.items(), key=lambda x: -x[1]):
            click.echo(f'extension,{ext},{size}')
    else:
        total_gb = total_size / (1024**3)
        click.echo(f'Total: {total_files} files, {total_gb:.1f} GB')
        if size_not_tracked:
            click.echo(f'  ({size_not_tracked} files without size data)')

        click.echo()
        click.echo('Top-level folders:')
        for folder, size in sorted(folders.items(), key=lambda x: -x[1]):
            pct = size / total_size * 100
            gb = size / (1024**3)
            click.echo(f'  {folder:30s} {gb:>8.2f} GB ({pct:5.1f}%)')

        click.echo()
        click.echo(f'Top {top} largest files:')
        for size, path in largest:
            gb = size / (1024**3)
            click.echo(f'  {gb:>8.2f} GB  {path}')

        click.echo()
        click.echo('Extension breakdown:')
        for ext, size in sorted(extensions.items(), key=lambda x: -x[1]):
            pct = size / total_size * 100
            gb = size / (1024**3)
            click.echo(f'  .{ext:10s} {gb:>8.2f} GB ({pct:5.1f}%)')


if __name__ == '__main__':
    cli()
