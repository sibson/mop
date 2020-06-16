from getpass import getpass

from b2sdk.v1 import SqliteAccountInfo, B2Api
from b2sdk.exception import FileAlreadyHidden
import click
import plyvel

import leveldb


@click.group()
def cli():
    pass


@cli.command()
def login():
    keyid = getpass('Key ID:')
    appkey = getpass('Application Key:')

    info = SqliteAccountInfo()
    b2 = B2Api(info)
    b2.authorize_account("production", keyid, appkey)


@cli.command()
@click.option('--folder', default='')
@click.option('--recursive', is_flag=True)
@click.option('--max-count', default=5)
@click.argument('bucket')
def sha1sum(folder, recursive, max_count, bucket):
    info = SqliteAccountInfo()
    b2 = B2Api(info)

    bucket = b2.get_bucket_by_name(bucket)
    for fi, dirname in bucket.ls(folder_to_list=folder, recursive=recursive, fetch_count=max_count):
        print(f'{fi.content_sha1} {fi.file_name}')


@cli.command()
@click.argument('bucket')
@click.option('--dbpath', default='.mop.db')
@click.option('--folder', default='')
@click.option('--recursive', is_flag=True)
@click.option('--max-count', default=5)
def index(bucket, dbpath, folder, recursive, max_count):
    info = SqliteAccountInfo()
    b2 = B2Api(info)
    bucket = b2.get_bucket_by_name(bucket)

    ldb = plyvel.DB(dbpath)

    for fi, dirname in bucket.ls(folder_to_list=folder, recursive=recursive, fetch_count=max_count):
        print(f'{fi.content_sha1} {fi.file_name}')
        leveldb.add_file(ldb, fi.content_sha1, fi.file_name)


@cli.command()
@click.argument('bucket')
@click.argument('paths', type=click.File('r'))
def hide(bucket, paths):
    info = SqliteAccountInfo()
    b2 = B2Api(info)
    bucket = b2.get_bucket_by_name(bucket)

    for path in paths:
        print(path, end='')
        try:
            bucket.hide_file(path[:-1])
        except FileAlreadyHidden:
            pass

if __name__ == '__main__':
    cli()
