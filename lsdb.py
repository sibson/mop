import binascii, hashlib

import click
import plyvel


@click.command()
@click.argument('dbpath')
def lsdb(dbpath):
    ldb = plyvel.DB(dbpath)
    for sha, path in ldb:
        print(f'{sha.decode("UTF-8")} {path.decode("UTF-8")}')


if __name__ == '__main__':
    lsdb()
