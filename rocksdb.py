import click
import rocksdb


@click.group()
def cli():
    pass


@cli.command()
@click.argument('dbpath')
def mk(dbpath):
    ldb = rocksdb.DB(dbpath, rocksdb.Options(create_if_missing=True, error_if_exists=True))


@cli.command()
@click.argument('dbpath')
def rm(dbpath):
    raise NotImplementedError


@cli.command()
@click.option('--dbpath', default='.mop.rocksdb')
def ls(dbpath):
    ldb = rocksdb.DB(dbpath)
    for sha, path in ldb:
        print(f'{sha.decode("UTF-8")} {path.decode("UTF-8")}')


if __name__ == '__main__':
    cli()
