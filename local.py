import os, hashlib

import click
import plyvel


def sha1(filename):
    with open(filename,"rb") as f:
        sha = hashlib.sha1(f.read()).hexdigest();
    return sha


def add_file(ldb, filename):
    sha = sha1(filename)
    ldb.put(sha.encode('UTF-8'), filename.encode('UTF-8'))


@click.group()
def cli():
    pass


@cli.command()
@click.option('--recursive')
def ls(path, recursive):
    for dirpath, dirnames, filenames in os.walk(path):
        for f in filenames:
            filename = os.path.join(dirpath, f)
            print(f'{sha1(filename)}: {filename}')


@cli.command()
@click.option('--dbpath', default='.mop.db')
@click.argument('path')
def add(dbpath, path, recurse=False):
    ldb = plyvel.DB(dbpath, create_if_missing=True)

    for dirpath, dirnames, filenames in os.walk(path):
        for filename in filenames:
            add_file(ldb, os.path.join(dirpath, filename))


if __name__ == '__main__':
    cli()
