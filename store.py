#!/usr/bin/env python3
import sqlite3, os, json, fnmatch

import click


SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    uri   TEXT PRIMARY KEY,
    sha1  TEXT NOT NULL,
    path  TEXT NOT NULL,
    size  INTEGER,
    extra TEXT
);
CREATE INDEX IF NOT EXISTS idx_files_sha1 ON files(sha1);
"""


def dumps(obj):
    return json.dumps(obj)


def loads(s):
    return json.loads(s)


def connect(dbpath, create=False):
    """Open sqlite db at dbpath.

    Sets PRAGMA journal_mode=WAL, synchronous=NORMAL. If create, runs SCHEMA.
    Returns sqlite3.Connection. Caller controls transactions/commit.
    """
    conn = sqlite3.connect(dbpath)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    if create:
        conn.executescript(SCHEMA)
    return conn


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

    def write(self, conn):
        """One INSERT OR REPLACE INTO files. Does NOT commit.

        size is lifted out of kwargs into its own column; the remaining kwkeys
        are JSON-serialized into extra.
        """
        size = getattr(self, 'size', None)
        extra = {k: getattr(self, k) for k in self.kwkeys if k != 'size'}
        extra_json = dumps(extra) if extra else None
        conn.execute(
            'INSERT OR REPLACE INTO files(uri, sha1, path, size, extra) '
            'VALUES(?, ?, ?, ?, ?)',
            (self.uri, self.sha1, self.path, size, extra_json))


def _row_to_json(uri, path, sha1, size, extra):
    """Reconstruct the meta JSON blob in the same key order as __json__:
    uri, path, sha1, then extra kwkeys (in stored order), then size (if set).
    """
    dct = {'uri': uri, 'path': path, 'sha1': sha1}
    if extra:
        dct.update(loads(extra))
    if size is not None:
        dct['size'] = size
    return dct


@click.group()
def cli():
    pass


@cli.command()
@click.argument('dbpath')
def mk(dbpath):
    if os.path.exists(dbpath):
        raise click.ClickException(f'{dbpath} already exists')
    conn = connect(dbpath, create=True)
    conn.commit()
    conn.close()


@cli.command()
@click.argument('dbpath')
def rm(dbpath):
    for p in (dbpath, dbpath + '-wal', dbpath + '-shm'):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass


@cli.command()
@click.argument('dbpath')
def truncate(dbpath):
    conn = connect(dbpath, create=True)
    conn.execute('DELETE FROM files')
    conn.commit()
    conn.close()


@cli.command()
@click.argument('dbpath')
@click.argument('wildcard', required=False)
def ls(dbpath, wildcard):
    conn = connect(dbpath)
    for sha1, uri in conn.execute('SELECT sha1, uri FROM files ORDER BY sha1'):
        if wildcard and not fnmatch.fnmatch(uri, wildcard):
            continue
        print(f'{sha1}\t{uri}')
    conn.close()


@cli.command()
@click.argument('dbpath')
def meta(dbpath):
    conn = connect(dbpath)
    for uri, path, sha1, size, extra in conn.execute(
            'SELECT uri, path, sha1, size, extra FROM files'):
        print(dumps(_row_to_json(uri, path, sha1, size, extra)))
    conn.close()


@cli.command()
@click.argument('target')
@click.argument('source', nargs=-1)
def missing(target, source):
    conn = connect(target)
    for srcname in source:
        conn.execute('ATTACH DATABASE ? AS src', (srcname,))
        rows = conn.execute(
            'SELECT sha1, uri FROM src.files '
            'WHERE sha1 NOT IN (SELECT sha1 FROM main.files)')
        for sha1, uri in rows:
            print(f'{srcname}\t{uri}')
        conn.execute('DETACH DATABASE src')
    conn.close()


@cli.command()
@click.argument('target')
@click.argument('source', nargs=-1)
def present(target, source):
    conn = connect(target)
    for srcname in source:
        conn.execute('ATTACH DATABASE ? AS src', (srcname,))
        rows = conn.execute(
            'SELECT sha1, uri FROM src.files '
            'WHERE sha1 IN (SELECT sha1 FROM main.files)')
        for sha1, uri in rows:
            print(f'{srcname}\t{uri}')
        conn.execute('DETACH DATABASE src')
    conn.close()


@cli.command()
@click.argument('target')
@click.argument('source', nargs=-1)
def merge(target, source):
    conn = connect(target, create=True)
    for srcname in source:
        conn.execute('ATTACH DATABASE ? AS src', (srcname,))
        for (uri,) in conn.execute('SELECT uri FROM src.files'):
            click.echo(f'{uri}')
        # target-wins: the INSERT OR IGNORE below never overwrites an existing
        # target uri. When the source disagrees on a uri's content hash, surface
        # it on stderr so the (kept-target / dropped-source) choice is auditable
        # instead of silent.
        for uri, tsha, ssha in conn.execute(
                'SELECT s.uri, m.sha1, s.sha1 FROM src.files s '
                'JOIN main.files m ON s.uri = m.uri WHERE m.sha1 != s.sha1'):
            click.echo(f'conflict {uri}: keeping target sha1 {tsha}, '
                       f'ignoring source {ssha}', err=True)
        conn.execute(
            'INSERT OR IGNORE INTO main.files(uri, sha1, path, size, extra) '
            'SELECT uri, sha1, path, size, extra FROM src.files')
        # commit before DETACH: an attached WAL-mode source holds a read lock
        # for the open transaction, and DETACH fails while it is held.
        conn.commit()
        conn.execute('DETACH DATABASE src')
    conn.close()


@cli.command()
@click.argument('dbpath')
def dupes(dbpath):
    conn = connect(dbpath)
    rows = conn.execute(
        "SELECT sha1, GROUP_CONCAT(uri, ' ') FROM files "
        "GROUP BY sha1 HAVING COUNT(*) > 1 ORDER BY sha1")
    for sha1, uris in rows:
        print(f'{sha1}\t{uris}')
    conn.close()


@cli.command()
@click.argument('dbpath')
@click.option('--top', default=10, help='Number of top folders/files to show')
@click.option('--csv', is_flag=True, help='Output as CSV')
def usage(dbpath, top, csv):
    """Analyze storage usage from an indexed database."""
    conn = connect(dbpath)
    folders = {}
    extensions = {}
    largest = []
    total_files = 0
    total_size = 0
    size_not_tracked = 0

    for path, size in conn.execute('SELECT path, size FROM files'):
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
    conn.close()

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
