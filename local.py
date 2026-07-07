#!/usr/bin/env python3

import os, hashlib

import click

import store


def sha1(filename, num_blocks=128):
    h = hashlib.sha1()
    with open(filename,'rb') as f:
        for chunk in iter(lambda: f.read(num_blocks*h.block_size), b''):
            h.update(chunk)
    return h.hexdigest()


@click.group()
def cli():
    pass


@cli.command()
@click.argument('path')
@click.option('--recursive')
def sha1sum(path, recursive):
    for dirpath, dirnames, filenames in os.walk(path):
        for f in filenames:
            filename = os.path.join(dirpath, f)
            print(f'{sha1(filename)}: {filename}')


@cli.command()
@click.option('--dbpath', default='.mop.db')
@click.option('--skip-existing', is_flag=True,
              help='Skip files already present in the index (resume a partial run)')
@click.argument('path')
def index(dbpath, skip_existing, path):
    conn = store.connect(dbpath, create=True)

    skipped = errored = 0
    for dirpath, dirnames, filenames in os.walk(path):
        for filename in filenames:
            fpath = os.path.abspath(os.path.join(dirpath, filename))
            uri = f'file://{fpath}'

            if skip_existing and conn.execute(
                    'SELECT 1 FROM files WHERE uri=?', (uri,)).fetchone() is not None:
                skipped += 1
                continue

            try:
                sha = sha1(fpath)
                size = os.path.getsize(fpath)
            except OSError as e:
                errored += 1
                click.echo(f'SKIP {fpath}: {e}', err=True)
                continue

            print(f'{sha} {filename}')
            md = store.FileMetaData(uri, sha, fpath, fileName=filename, size=size)
            md.write(conn)

    conn.commit()
    conn.close()

    if skipped or errored:
        click.echo(f'done: {skipped} already-indexed skipped, '
                   f'{errored} unreadable skipped', err=True)


if __name__ == '__main__':
    cli()
