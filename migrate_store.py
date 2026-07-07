#!/usr/bin/env python3
"""One-time migration: LevelDB (plyvel) index dir -> native SQLite (store.py).

Reads the `meta` prefixed_db namespace of an old leveldb dir, reconstructs each
FileMetaData, and writes it into a fresh sqlite db via store.FileMetaData.write.
plyvel is only needed here to read the old dirs; it is not imported by store.py.
"""
import os

import click
import plyvel

import store


@click.command()
@click.argument('old_leveldb_dir')
@click.argument('new_sqlite_db')
@click.option('--force', is_flag=True,
              help='Overwrite NEW_SQLITE_DB if it already exists')
def migrate(old_leveldb_dir, new_sqlite_db, force):
    """Migrate OLD_LEVELDB_DIR (a plyvel dir) into NEW_SQLITE_DB (a sqlite file)."""
    if os.path.exists(new_sqlite_db):
        if not force:
            raise click.ClickException(
                f'{new_sqlite_db} already exists (use --force to overwrite)')
        for p in (new_sqlite_db, new_sqlite_db + '-wal', new_sqlite_db + '-shm'):
            try:
                os.remove(p)
            except FileNotFoundError:
                pass

    db = plyvel.DB(old_leveldb_dir)
    meta = db.prefixed_db(b'meta')

    conn = store.connect(new_sqlite_db, create=True)

    count = 0
    for k, v in meta.iterator():
        data = store.loads(v.decode())
        md = store.FileMetaData.construct(data)
        md.write(conn)
        count += 1
        if count % 10000 == 0:
            click.echo(f'  {count} rows...', err=True)

    conn.commit()
    conn.close()
    db.close()

    click.echo(f'done: migrated {count} rows from {old_leveldb_dir} '
               f'-> {new_sqlite_db}')


if __name__ == '__main__':
    migrate()
