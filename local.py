#!/usr/bin/env python3

import os, hashlib, shutil, sys
from datetime import datetime

import click

import store


EMPTY_SHA1 = 'da39a3ee5e6b4b0d3255bfef95601890afd80709'


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
@click.argument('path')
@click.option('--recursive')
def sha1sum(path, recursive):
    for dirpath, dirnames, filenames in os.walk(path):
        for f in filenames:
            filename = os.path.join(dirpath, f)
            print(f'{sha1(filename)}: {filename}')


@cli.command()
@click.option('--dbpath', default='.mop.db')
@click.option('--skip-existing', is_flag=True,
              help='Skip files already present in the index (resume a partial run)')
@click.argument('path')
def index(dbpath, skip_existing, path):
    conn = store.connect(dbpath, create=True)

    skipped = errored = 0
    for dirpath, dirnames, filenames in os.walk(path):
        for filename in filenames:
            fpath = os.path.abspath(os.path.join(dirpath, filename))
            uri = f'file://{fpath}'

            if skip_existing and conn.execute(
                    'SELECT 1 FROM files WHERE uri=?', (uri,)).fetchone() is not None:
                skipped += 1
                continue

            try:
                sha = sha1(fpath)
                size = os.path.getsize(fpath)
            except OSError as e:
                errored += 1
                click.echo(f'SKIP {fpath}: {e}', err=True)
                continue

            print(f'{sha} {filename}')
            md = store.FileMetaData(uri, sha, fpath, fileName=filename, size=size)
            md.write(conn)

    conn.commit()
    conn.close()

    if skipped or errored:
        click.echo(f'done: {skipped} already-indexed skipped, '
                   f'{errored} unreadable skipped', err=True)


# ---------------------------------------------------------------------------
# Trash: a recoverable alternative to rm, shared by the `trash` and `dedup`
# commands. Files move into ROOT/<trash-dir> keeping their relative layout, and
# every move is appended to <trash-dir>/trashed.log.
# ---------------------------------------------------------------------------

def _resolve_under_root(root, raw, trash_dir):
    """Map an input path to (abspath, rel-to-root), or (None, reason)."""
    src = raw[len('file://'):] if raw.startswith('file://') else raw
    if not os.path.isabs(src):
        src = os.path.join(root, src)
    src = os.path.abspath(src)
    rel = os.path.relpath(src, root)
    if rel == '.' or rel.startswith('..') or os.path.isabs(rel):
        return None, 'outside-root'
    if rel.split(os.sep)[0] == trash_dir:
        return None, 'already-trashed'
    return src, rel


def _trash_move(root, trash_dir, src, rel, log_lines):
    dst = os.path.join(root, trash_dir, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)
    log_lines.append(f'{datetime.now().isoformat(timespec="seconds")}\t{rel}')


def _flush_log(root, trash_dir, log_lines):
    if not log_lines:
        return
    troot = os.path.join(root, trash_dir)
    os.makedirs(troot, exist_ok=True)
    with open(os.path.join(troot, 'trashed.log'), 'a') as fh:
        fh.write('\n'.join(log_lines) + '\n')


@cli.command()
@click.option('--root', required=True,
              help='Base directory; only files under it may be trashed')
@click.option('--trash-dir', default='.trash',
              help='Recovery directory under ROOT (default: .trash)')
@click.option('--dry-run', is_flag=True, help='Show what would move, change nothing')
@click.argument('paths', nargs=-1)
def trash(root, trash_dir, dry_run, paths):
    """Move files into ROOT/<trash-dir> preserving their relative path.

    PATHS may be file:// URIs, absolute paths, or paths relative to ROOT; pass
    '-' to read newline-separated paths from stdin (pipeline style), e.g.

        cut -f2 confirmed.tsv | ./local.py trash --root /srv/shares/memories -
    """
    root = os.path.abspath(root)
    items = []
    for p in paths:
        if p == '-':
            items.extend(line.strip() for line in sys.stdin if line.strip())
        else:
            items.append(p)

    moved = skipped = errored = 0
    log_lines = []
    for raw in items:
        src, rel = _resolve_under_root(root, raw, trash_dir)
        if src is None:
            click.echo(f'SKIP {rel}: {raw}', err=True); skipped += 1; continue
        if not os.path.exists(src):
            click.echo(f'SKIP missing: {rel}', err=True); skipped += 1; continue
        if dry_run:
            click.echo(f'would trash: {rel}'); moved += 1; continue
        try:
            _trash_move(root, trash_dir, src, rel, log_lines)
            moved += 1; click.echo(f'trashed: {rel}')
        except OSError as e:
            errored += 1; click.echo(f'ERROR {rel}: {e}', err=True)

    _flush_log(root, trash_dir, log_lines)
    click.echo(f'{"dry-run: " if dry_run else ""}'
               f'{moved} trashed, {skipped} skipped, {errored} errored', err=True)


@cli.command()
@click.option('--dbpath', required=True, help='Index used to find candidate duplicate groups')
@click.option('--root', required=True,
              help='Mount root; resolves indexed paths and holds the trash dir')
@click.option('--removable-prefix', default='NEW/Seagate Exp/',
              help='Only files under this (root-relative) prefix are candidates for removal')
@click.option('--trash-dir', default='.trash')
@click.option('--limit', type=int, default=0,
              help='Stop after N confirmed removals (0 = all); useful for a cautious first batch')
@click.option('--apply', is_flag=True, help='Trash confirmed dupes (default: dry run, change nothing)')
def dedup(dbpath, root, removable_prefix, trash_dir, limit, apply):
    """Trash exact duplicates, re-verifying content with a FRESH hash.

    The index is used only to find candidate groups (files sharing a recorded
    sha1). Before removing anything, a fresh sha1 is recomputed from the current
    bytes of the removable copy and of a surviving copy OUTSIDE --removable-prefix;
    the removable is trashed only when those fresh hashes match. Each file is
    hashed at most once (a survivor shared by many duplicates is not re-read).
    Stale index rows or changed files surface as mismatches and are never
    removed. Dry run unless --apply.
    """
    conn = store.connect(dbpath)
    root = os.path.abspath(root)

    hcache = {}
    def fresh(path):
        if path not in hcache:
            try:
                hcache[path] = sha1(path)
            except OSError:
                hcache[path] = None
        return hcache[path]

    groups = conn.execute(
        'SELECT sha1 FROM files WHERE sha1 != ? GROUP BY sha1 HAVING COUNT(*) > 1',
        (EMPTY_SHA1,)).fetchall()

    confirmed = mismatch = missing = nosurv = 0
    bytes_freed = 0
    log_lines = []
    reached_limit = False
    for (sha,) in groups:
        if reached_limit:
            break
        rows = conn.execute('SELECT uri, size FROM files WHERE sha1 = ?', (sha,)).fetchall()
        removable, survivors = [], []
        for uri, size in rows:
            p = os.path.abspath(uri[len('file://'):] if uri.startswith('file://') else uri)
            rel = os.path.relpath(p, root)
            if not rel.startswith('..') and rel.startswith(removable_prefix):
                removable.append((p, rel, size))
            else:
                survivors.append(p)
        if not removable or not survivors:
            continue

        surv_hash = next((h for h in (fresh(sp) for sp in survivors) if h), None)
        if surv_hash is None:
            nosurv += 1
            continue

        for rp, rrel, size in removable:
            rh = fresh(rp)
            if rh is None:
                missing += 1
                continue
            if rh != surv_hash:
                mismatch += 1
                click.echo(f'MISMATCH (not trashing): {rrel}', err=True)
                continue
            confirmed += 1
            bytes_freed += size or 0
            if apply:
                try:
                    _trash_move(root, trash_dir, rp, rrel, log_lines)
                    click.echo(f'trashed: {rrel}')
                except OSError as e:
                    click.echo(f'ERROR {rrel}: {e}', err=True)
            else:
                click.echo(f'would trash: {rrel}')
            if limit and confirmed >= limit:
                reached_limit = True
                break

    _flush_log(root, trash_dir, log_lines)
    click.echo(f'{"" if apply else "dry-run: "}{confirmed} confirmed '
               f'({bytes_freed / 1024**3:.2f} GB), {mismatch} mismatch, '
               f'{missing} missing, {nosurv} groups without a live survivor', err=True)


if __name__ == '__main__':
    cli()
