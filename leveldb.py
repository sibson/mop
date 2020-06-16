import binascii, hashlib, json

import click
import plyvel

@click.group()
def cli():
    pass


def add_file(ldb, sha, filename):
    ldb.put(sha.encode(), filename.encode())


@cli.command()
@click.argument('dbpath')
def mk(dbpath):
    ldb = plyvel.DB(dbpath, create_if_missing=True, error_if_exists=True)


@cli.command()
@click.argument('dbpath')
def rm(dbpath):
    plyvel.destroy_db(dbpath)


@cli.command()
@click.argument('dbpath')
def ls(dbpath):
    ldb = plyvel.DB(dbpath)
    for sha, path in ldb:
        print(f'{sha.decode("UTF-8")} {path.decode("UTF-8")}')



@cli.command()
@click.argument('target')
@click.argument('source', nargs=-1)
def missing(target, sources):
    target = plyvel.DB(target)
    sources = [(s, plyvel.DB(s)) for s in sources]

    for srcname, src  in sources:
        for sha, path in src:
            if target.get(sha):
                continue

            print(f'{srcname}\t{path.decode()}')


@cli.command()
@click.argument('target')
@click.argument('source', nargs=-1)
def present(target, source):
    target = plyvel.DB(target)
    sources = [(s, plyvel.DB(s)) for s in source]

    for srcname, src  in sources:
        for sha, path in src:
            if target.get(sha):
                print(f'{srcname}\t{path.decode()}')


if __name__ == '__main__':
    cli()
