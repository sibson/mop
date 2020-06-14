import os, hashlib

import click
import plyvel


def add_file(ldb, filename):
    with open(filename,"rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest();
        ldb.put(sha.encode('UTF-8'), filename.encode('UTF-8'))


@click.command()
@click.argument('dbpath')
@click.argument('path')
def mklocal(dbpath, path, recurse=False):
    ldb = plyvel.DB(dbpath, create_if_missing=True)

    for dirpath, dirnames, filenames in os.walk(path):
        for f in filenames:
            add_file(ldb, os.path.join(dirpath, f))

if __name__ == '__main__'::w
    mklocal()
