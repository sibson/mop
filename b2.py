from getpass import getpass

from b2sdk.v1 import SqliteAccountInfo, B2Api
import click

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
@click.option('--max-count', default=5)
def ls(bucket, max_count):
    info = SqliteAccountInfo()
    b2 = B2Api(info)

    bucket = b2.get_bucket_by_name(bucket)
    for fi, dirname in bucket.ls(fetch_count=max_count):
        print(f'{fi.content_sha1} {fi.file_name}')

if __name__ == '__main__':
    cli()
