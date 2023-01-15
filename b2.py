#!/usr/bin/env python3
import sys
from getpass import getpass
import urllib

from b2sdk.v1 import SqliteAccountInfo, B2Api
from b2sdk.exception import FileAlreadyHidden, FileNotPresent
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
@click.option('--flat', is_flag=True)
@click.option('--max-count', default=5)
def index(bucket, dbpath, folder, recursive, flat, max_count):
    info = SqliteAccountInfo()
    b2 = B2Api(info)
    b2bucket = b2.get_bucket_by_name(bucket)

    recursive = recursive and not flat

    ldb = plyvel.DB(dbpath, create_if_missing=True)
    count = 0
    for fi, dirname in b2bucket.ls(folder_to_list=folder, recursive=recursive, fetch_count=max_count):
        uri = f'b2://{bucket}/{fi.file_name}'

        # sha1 for large files isn't stored in content_sha1 but rather in file_info /shrug
        sha = fi.content_sha1 if fi.content_sha1 != 'none' else fi.file_info['large_file_sha1']

        print(f'{sha} {fi.file_name} {fi.size / 1024 / 1024:.1f}Mb')
        md = leveldb.FileMetaData(uri, sha, fi.file_name, fileName=fi.file_name, bucket=bucket, fileId=fi.id_)
        md.write(ldb)
        count += 1

    print(f'Total Files {count}')

@cli.command()
@click.argument('bucket')
@click.argument('paths', type=click.File('r'))
def hide(bucket, paths):
    """ Hiding files in Backblaze allows the system lifecycle rules to operate and eventually age out files.


    paths: newline seperated list of file paths to hide
    """
    info = SqliteAccountInfo()
    b2 = B2Api(info)
    bucket = b2.get_bucket_by_name(bucket)

    for path in paths:
        path = path.strip('\n')
        if path.startswith('b2://'):
            path = urllib.parse.urlparse(path).path[1:]
        print(f'{path} ', end='')
        try:
            bucket.hide_file(path)
        except FileAlreadyHidden:
            pass
        except FileNotPresent:
            print('NOT FOUND')
            continue
        print('hidden')


if __name__ == '__main__':
    cli()
