from getpass import getpass

from b2sdk.v1 import SqliteAccountInfo, B2Api
import click
import plyvel


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
@click.argument('bucket')
@click.option('--folder', default='')
@click.option('--recursive', default=False)
@click.option('--max-count', default=5)
def ls(bucket, folder, recursive, max_count):
    info = SqliteAccountInfo()
    b2 = B2Api(info)

    bucket = b2.get_bucket_by_name(bucket)
    for fi, dirname in bucket.ls(folder_to_list=folder, recursive=False, fetch_count=max_count):
        print(f'{fi.content_sha1} {fi.file_name}')


@cli.command()
@click.argument('bucket')
@click.option('--dbpath', default='.mop.db')
@click.option('--folder', default='')
@click.option('--recursive', default=False)
@click.option('--max-count', default=5)
def add(bucket, dbpath, folder, recursive, max_count):
    info = SqliteAccountInfo()
    b2 = B2Api(info)
    bucket = b2.get_bucket_by_name(bucket)

    ldb = plyvel.DB(dbpath)

    for fi, dirname in bucket.ls(folder_to_list=folder, recursive=False, fetch_count=max_count):
        print(f'{fi.content_sha1} {fi.file_name}')
        ldb.put(fi.content_sha1.encode('UTF-8'), fi.file_name.encode('UTF-8'))

if __name__ == '__main__':
    cli()
