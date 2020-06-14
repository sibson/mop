import binascii, hashlib

import click
import plyvel

@click.group()
def cli():
    pass


@cli.command()
@click.argument('dbpath')
def mk(dbpath):
    ldb = plyvel.DB(dbpath, create_if_missing=True, error_if_exists=True)


@cli.command()
@click.argument('dbpath')
def rm(dbpath):
    plyvel.destroy_db(dbpath)


@cli.command()
@click.option('--dbpath', default='.mop.db')
def ls(dbpath):
    ldb = plyvel.DB(dbpath)
    for sha, path in ldb:
        print(f'{sha.decode("UTF-8")} {path.decode("UTF-8")}')


if __name__ == '__main__':
    cli()
