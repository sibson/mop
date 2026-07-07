#!/usr/bin/env python3
import sys
from getpass import getpass

from b2sdk.v1 import SqliteAccountInfo, B2Api
from b2sdk.v1.exception import FileAlreadyHidden, FileNotPresent
import click


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
@click.option('--max-count', default=10000)
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
@click.option('--max-count', default=10000)
def index(bucket, dbpath, folder, recursive, flat, max_count):
    import plyvel

    info = SqliteAccountInfo()
    b2 = B2Api(info)
    b2bucket = b2.get_bucket_by_name(bucket)

    recursive = recursive and not flat

    # wipe out existing DB because it might contain files that no longer exist
    plyvel.destroy_db(dbpath)
    ldb = plyvel.DB(dbpath, create_if_missing=True)
    count = 0
    size = 0
    for fi, dirname in b2bucket.ls(folder_to_list=folder, recursive=recursive, fetch_count=max_count):
        uri = f'b2://{bucket}/{fi.file_name}'

        # sha1 for large files isn't stored in content_sha1 but rather in file_info /shrug
        sha = fi.content_sha1 if fi.content_sha1 != 'none' else fi.file_info['large_file_sha1']

        print(f'{sha} {fi.file_name} {fi.size / 1024 / 1024:.1f}Mb')
        md = leveldb.FileMetaData(uri, sha, fi.file_name, fileName=fi.file_name, bucket=bucket, fileId=fi.id_, size=fi.size)
        md.write(ldb)
        count += 1
        size += fi.size

    print(f'Total {count} files, {size / 1024 / 1024}MB')


@cli.command()
@click.argument('bucket')
@click.option('--folder', default='')
@click.option('--recursive', is_flag=True, default=True)
@click.option('--top', default=10, help='Number of top folders/files to show')
@click.option('--csv', is_flag=True, help='Output as CSV')
@click.option('--max-count', default=10000, help='Files per API call')
def usage(bucket, folder, recursive, top, csv, max_count):
    """Show storage usage for a B2 bucket (live query, no DB)."""
    info = SqliteAccountInfo()
    b2 = B2Api(info)
    bucket = b2.get_bucket_by_name(bucket)

    total_count = 0
    total_size = 0
    folders = {}
    extensions = {}
    largest = []

    for fi, dirname in bucket.ls(folder_to_list=folder, recursive=recursive, fetch_count=max_count):
        if fi.action != 'upload':
            continue
        total_count += 1
        total_size += fi.size

        top_folder = fi.file_name.split('/')[0]
        folders[top_folder] = folders.get(top_folder, 0) + fi.size

        ext = fi.file_name.rsplit('.', 1)[-1] if '.' in fi.file_name else '(none)'
        extensions[ext] = extensions.get(ext, 0) + fi.size

        largest.append((fi.file_name, fi.size))

    largest.sort(key=lambda x: x[1], reverse=True)
    largest = largest[:top]

    if csv:
        click.echo('category,name,size_bytes')
        click.echo(f'summary,total,{total_size}')
        click.echo(f'summary,count,{total_count}')
        for folder, fsize in sorted(folders.items(), key=lambda x: x[1], reverse=True):
            click.echo(f'folder,{folder},{fsize}')
        for fname, fsize in largest:
            click.echo(f'file,{fname},{fsize}')
        for ext, esize in sorted(extensions.items(), key=lambda x: x[1], reverse=True):
            click.echo(f'extension,{ext},{esize}')
    else:
        click.echo(f'Total: {total_count} files, {total_size / 1024 / 1024 / 1024:.1f} GB')
        click.echo()
        click.echo('Top-level folders:')
        for folder, fsize in sorted(folders.items(), key=lambda x: x[1], reverse=True):
            click.echo(f'  {folder}: {fsize / 1024 / 1024 / 1024:.2f} GB')
        click.echo()
        click.echo(f'Top {top} largest files:')
        for fname, fsize in largest:
            click.echo(f'  {fname}: {fsize / 1024 / 1024:.1f} MB')
        click.echo()
        click.echo('Extension breakdown:')
        for ext, esize in sorted(extensions.items(), key=lambda x: x[1], reverse=True):
            click.echo(f'  .{ext}: {esize / 1024 / 1024 / 1024:.2f} GB')


@cli.command()
@click.argument('bucket')
@click.argument('paths', type=click.File('r'))
@click.option('--dry-run', is_flag=True, help='Print paths that would be hidden, without hiding them')
@click.option('--log', 'log_path', default=None, help='Append successfully-hidden paths to this file')
def hide(bucket, paths, dry_run, log_path):
    """ Hiding files in Backblaze allows the system lifecycle rules to operate and eventually age out files.


    paths: newline seperated list of file paths to hide
    """
    info = SqliteAccountInfo()
    b2 = B2Api(info)
    bucket = b2.get_bucket_by_name(bucket)

    log_file = open(log_path, 'a') if log_path else None

    hidden_count = 0
    not_found_count = 0
    already_hidden_count = 0

    try:
        for path in paths:
            path = path.strip('\n')
            if path.startswith('b2://'):
                path = '/'.join(path.split('/')[3:])

            if dry_run:
                print(f'{path} would hide')
                continue

            print(f'{path} ', end='')
            try:
                bucket.hide_file(path)
            except FileAlreadyHidden:
                already_hidden_count += 1
                print('already hidden')
                continue
            except FileNotPresent:
                not_found_count += 1
                print('NOT FOUND')
                continue
            hidden_count += 1
            print('hidden')
            if log_file:
                log_file.write(f'{path}\thidden\n')
    finally:
        if log_file:
            log_file.close()

    if not dry_run:
        print(f'Total: {hidden_count} hidden, {not_found_count} not found, {already_hidden_count} already hidden')


if __name__ == '__main__':
    cli()
