#!/usr/bin/env python3

import os, hashlib

import click
import plyvel

import leveldb


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
@click.option('--recursive')
def sha1sum(path, recursive):
    for dirpath, dirnames, filenames in os.walk(path):
        for f in filenames:
            filename = os.path.join(dirpath, f)
            print(f'{sha1(filename)}: {filename}')


@cli.command()
@click.option('--dbpath', default='.mop.db')
@click.argument('path')
def index(dbpath, path, recurse=False):
    ldb = plyvel.DB(dbpath, create_if_missing=True)

    for dirpath, dirnames, filenames in os.walk(path):
        for filename in filenames:
            path = os.path.join(dirpath, filename)
            path = os.path.abspath(path)
            uri = f'file://{path}'
            sha = sha1(path)

            print(f'{sha} {filename}')
            md = leveldb.FileMetaData(uri, sha, path, fileName=filename)
            md.write(ldb)


if __name__ == '__main__':
    cli()
