#!/usr/bin/env python3
import sys
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
@click.option('--dbpath', default='.b2.db')
@click.option('--folder', default='')
@click.option('--recursive', is_flag=True)
@click.option('--max-count', default=5)
def index(bucket, dbpath, folder, recursive, max_count):
    info = SqliteAccountInfo()
    b2 = B2Api(info)
    b2bucket = b2.get_bucket_by_name(bucket)

    ldb = plyvel.DB(dbpath, create_if_missing=True)
    for fi, dirname in b2bucket.ls(folder_to_list=folder, recursive=recursive, fetch_count=max_count):
        uri = f'b2://{bucket}/{fi.file_name}'

        print(f'{fi.content_sha1} {fi.file_name}')
        md = leveldb.FileMetaData(uri, fi.content_sha1, fi.file_name, fileName=fi.file_name, bucket=bucket, fileId=fi.id_)
        md.write(ldb)


@cli.command()
@click.argument('bucket')
@click.argument('paths', type=click.File('r'))
def hide(bucket, paths):
    info = SqliteAccountInfo()
    b2 = B2Api(info)
    bucket = b2.get_bucket_by_name(bucket)

    for path in paths:
        path = path.strip('\n')
        print(f'{path}.. ', end='')
        try:
            bucket.hide_file(path)
        except FileAlreadyHidden:
            pass

        print('hidden')


if __name__ == '__main__':
    cli()
