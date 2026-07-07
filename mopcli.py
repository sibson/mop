#!/usr/bin/env python3
import click

from b2 import cli as b2_cli
from local import cli as local_cli
import leveldb


@click.group()
def cli():
    pass


cli.add_command(b2_cli, 'b2')
cli.add_command(local_cli, 'local')
cli.add_command(leveldb.cli, 'leveldb')


if __name__ == '__main__':
    cli()
