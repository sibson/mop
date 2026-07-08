from unittest.mock import patch

import pytest
from click.testing import CliRunner

import store
from local import cli as local_cli
from store import cli as store_cli


@pytest.fixture
def runner():
    return CliRunner()


class TestSha1sum:

    def test_sha1sum_command(self, capsys):
        with patch('local.os.walk') as mock_walk:
            mock_walk.return_value = [
                ('/base', [], ['a.txt', 'b.jpg']),
            ]
            with patch('local.sha1') as mock_sha1:
                mock_sha1.side_effect = ['sha_a', 'sha_b']

                raw_func = local_cli.commands['sha1sum'].callback
                raw_func('/base', None)

                captured = capsys.readouterr()
                assert 'sha_a: /base/a.txt' in captured.out
                assert 'sha_b: /base/b.jpg' in captured.out


class TestIndex:

    def _tree(self, tmp_path, files):
        """Create real files under tmp_path; return the root dir path (str)."""
        root = tmp_path / 'tree'
        for rel, content in files.items():
            f = root / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(content)
        return str(root)

    def test_index_writes_real_db(self, tmp_path, runner):
        root = self._tree(tmp_path, {
            'a.txt': b'hello',
            'b.jpg': b'world',
        })
        dbpath = str(tmp_path / 'idx.db')

        result = runner.invoke(local_cli, ['index', '--dbpath', dbpath, root])
        assert result.exit_code == 0
        assert 'a.txt' in result.output
        assert 'b.jpg' in result.output

        conn = store.connect(dbpath)
        rows = conn.execute('SELECT uri, sha1, path, size, extra FROM files').fetchall()
        conn.close()
        assert len(rows) == 2
        by_name = {r[0].rsplit('/', 1)[-1]: r for r in rows}
        # size column populated; fileName captured in extra.
        assert by_name['a.txt'][3] == len(b'hello')
        assert store.loads(by_name['a.txt'][4]) == {'fileName': 'a.txt'}
        # uri is a file:// absolute path
        assert by_name['a.txt'][0].startswith('file://')

    def test_index_then_store_ls_reads_back(self, tmp_path, runner):
        root = self._tree(tmp_path, {'x.txt': b'data'})
        dbpath = str(tmp_path / 'idx2.db')

        result = runner.invoke(local_cli, ['index', '--dbpath', dbpath, root])
        assert result.exit_code == 0

        # store ls reads the db the index wrote.
        import hashlib
        sha = hashlib.sha1(b'data').hexdigest()
        ls = runner.invoke(store_cli, ['ls', dbpath])
        assert ls.exit_code == 0
        assert sha in ls.output
        assert 'x.txt' in ls.output

    def test_index_then_store_dupes(self, tmp_path, runner):
        # Two files with identical content share a sha1 -> dupes reports them.
        root = self._tree(tmp_path, {
            'one.txt': b'same-content',
            'sub/two.txt': b'same-content',
            'uniq.txt': b'different',
        })
        dbpath = str(tmp_path / 'idx3.db')
        assert runner.invoke(local_cli, ['index', '--dbpath', dbpath, root]).exit_code == 0

        dupes = runner.invoke(store_cli, ['dupes', dbpath])
        assert dupes.exit_code == 0
        lines = [l for l in dupes.output.splitlines() if l]
        assert len(lines) == 1  # exactly one duplicated sha1 group
        sha1, uris = lines[0].split('\t')
        uri_set = set(uris.split(' '))
        assert len(uri_set) == 2
        assert any(u.endswith('one.txt') for u in uri_set)
        assert any(u.endswith('two.txt') for u in uri_set)

    def test_index_default_dbpath(self, tmp_path, runner):
        root = self._tree(tmp_path, {'a.txt': b'hi'})
        # isolate default '.mop.db' inside the CliRunner filesystem
        with runner.isolated_filesystem():
            result = runner.invoke(local_cli, ['index', root])
            assert result.exit_code == 0
            import os
            assert os.path.exists('.mop.db')

    def test_index_skip_existing(self, tmp_path, runner):
        root = self._tree(tmp_path, {'a.txt': b'hello'})
        dbpath = str(tmp_path / 'skip.db')

        first = runner.invoke(local_cli, ['index', '--dbpath', dbpath, root])
        assert first.exit_code == 0
        assert 'a.txt' in first.output

        # Second run with --skip-existing: already-indexed file is skipped, not
        # re-hashed/re-printed, and reported in the summary.
        with patch('local.sha1') as mock_sha1:
            second = runner.invoke(
                local_cli, ['index', '--skip-existing', '--dbpath', dbpath, root])
        assert second.exit_code == 0
        mock_sha1.assert_not_called()
        assert 'a.txt' not in second.output
        assert '1 already-indexed skipped' in second.output

        conn = store.connect(dbpath)
        assert conn.execute('SELECT COUNT(*) FROM files').fetchone()[0] == 1
        conn.close()

    def test_index_without_skip_existing_reindexes(self, tmp_path, runner):
        root = self._tree(tmp_path, {'a.txt': b'hello'})
        dbpath = str(tmp_path / 'reidx.db')
        assert runner.invoke(local_cli, ['index', '--dbpath', dbpath, root]).exit_code == 0
        # Re-run without the flag: file is re-hashed and upserted (still 1 row).
        second = runner.invoke(local_cli, ['index', '--dbpath', dbpath, root])
        assert second.exit_code == 0
        assert 'a.txt' in second.output
        conn = store.connect(dbpath)
        assert conn.execute('SELECT COUNT(*) FROM files').fetchone()[0] == 1
        conn.close()

    def test_index_handles_nested_dirs(self, tmp_path, runner):
        root = self._tree(tmp_path, {'sub/deep.txt': b'deep'})
        dbpath = str(tmp_path / 'nested.db')
        result = runner.invoke(local_cli, ['index', '--dbpath', dbpath, root])
        assert result.exit_code == 0
        assert 'deep.txt' in result.output
        conn = store.connect(dbpath)
        uris = [r[0] for r in conn.execute('SELECT uri FROM files')]
        conn.close()
        assert len(uris) == 1
        assert uris[0].endswith('sub/deep.txt')

    def test_index_skips_unreadable_file(self, tmp_path, runner):
        # regression: one unreadable file must not abort the whole walk.
        root = self._tree(tmp_path, {'bad.txt': b'x', 'good.txt': b'y'})
        dbpath = str(tmp_path / 'unreadable.db')

        from local import sha1 as real_local_sha1

        def fake_sha1(path, *a, **kw):
            if path.endswith('bad.txt'):
                raise PermissionError(13, 'denied')
            return real_local_sha1(path)

        with patch('local.sha1', side_effect=fake_sha1):
            result = runner.invoke(local_cli, ['index', '--dbpath', dbpath, root])

        assert result.exit_code == 0
        assert 'good.txt' in result.output
        conn = store.connect(dbpath)
        uris = [r[0] for r in conn.execute('SELECT uri FROM files')]
        conn.close()
        assert len(uris) == 1
        assert uris[0].endswith('good.txt')


def _tree(tmp_path, files):
    root = tmp_path / 'tree'
    for rel, content in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(content)
    return str(root)


class TestTrash:

    def test_moves_preserving_layout_and_logs(self, tmp_path, runner):
        root = _tree(tmp_path, {'a/b/c.txt': b'x'})
        r = runner.invoke(local_cli, ['trash', '--root', root, 'a/b/c.txt'])
        assert r.exit_code == 0
        import os
        assert not os.path.exists(os.path.join(root, 'a/b/c.txt'))
        assert os.path.exists(os.path.join(root, '.trash/a/b/c.txt'))
        log = open(os.path.join(root, '.trash/trashed.log')).read()
        assert 'a/b/c.txt' in log

    def test_dry_run_changes_nothing(self, tmp_path, runner):
        root = _tree(tmp_path, {'x.txt': b'x'})
        r = runner.invoke(local_cli, ['trash', '--root', root, '--dry-run', 'x.txt'])
        assert r.exit_code == 0
        assert 'would trash: x.txt' in r.output
        import os
        assert os.path.exists(os.path.join(root, 'x.txt'))
        assert not os.path.exists(os.path.join(root, '.trash'))

    def test_skips_outside_root(self, tmp_path, runner):
        root = _tree(tmp_path, {'x.txt': b'x'})
        outside = str(tmp_path / 'elsewhere.txt')
        (tmp_path / 'elsewhere.txt').write_bytes(b'y')
        r = runner.invoke(local_cli, ['trash', '--root', root, outside])
        assert r.exit_code == 0
        assert 'outside-root' in r.output
        import os
        assert os.path.exists(outside)

    def test_skips_missing(self, tmp_path, runner):
        root = _tree(tmp_path, {'here.txt': b'x'})
        r = runner.invoke(local_cli, ['trash', '--root', root, 'gone.txt'])
        assert r.exit_code == 0
        assert 'SKIP missing' in r.output

    def test_reads_stdin(self, tmp_path, runner):
        root = _tree(tmp_path, {'one.txt': b'1', 'two.txt': b'2'})
        r = runner.invoke(local_cli, ['trash', '--root', root, '-'],
                          input='one.txt\ntwo.txt\n')
        assert r.exit_code == 0
        import os
        assert os.path.exists(os.path.join(root, '.trash/one.txt'))
        assert os.path.exists(os.path.join(root, '.trash/two.txt'))


class TestDedup:

    def _indexed(self, tmp_path, runner, files):
        root = _tree(tmp_path, files)
        dbpath = str(tmp_path / 'idx.db')
        assert runner.invoke(local_cli, ['index', '--dbpath', dbpath, root]).exit_code == 0
        return root, dbpath

    def test_apply_trashes_removable_keeps_survivor(self, tmp_path, runner):
        root, db = self._indexed(tmp_path, runner, {
            '2015/photo.jpg': b'same-bytes',
            'NEW/Seagate Exp/photo.jpg': b'same-bytes',
        })
        r = runner.invoke(local_cli, ['dedup', '--dbpath', db, '--root', root, '--apply'])
        assert r.exit_code == 0
        assert '1 confirmed' in r.output
        import os
        assert not os.path.exists(os.path.join(root, 'NEW/Seagate Exp/photo.jpg'))
        assert os.path.exists(os.path.join(root, '.trash/NEW/Seagate Exp/photo.jpg'))
        assert os.path.exists(os.path.join(root, '2015/photo.jpg'))  # survivor stays

    def test_dry_run_does_not_move(self, tmp_path, runner):
        root, db = self._indexed(tmp_path, runner, {
            '2015/p.jpg': b'dup',
            'NEW/Seagate Exp/p.jpg': b'dup',
        })
        r = runner.invoke(local_cli, ['dedup', '--dbpath', db, '--root', root])
        assert r.exit_code == 0
        assert 'would trash' in r.output
        import os
        assert os.path.exists(os.path.join(root, 'NEW/Seagate Exp/p.jpg'))
        assert not os.path.exists(os.path.join(root, '.trash'))

    def test_fresh_hash_mismatch_is_not_trashed(self, tmp_path, runner):
        # indexed as identical, but the removable file changed after indexing:
        # the fresh recompute must catch it and refuse to delete.
        root, db = self._indexed(tmp_path, runner, {
            '2015/p.jpg': b'original',
            'NEW/Seagate Exp/p.jpg': b'original',
        })
        import os
        with open(os.path.join(root, 'NEW/Seagate Exp/p.jpg'), 'wb') as fh:
            fh.write(b'TAMPERED-now-different')
        r = runner.invoke(local_cli, ['dedup', '--dbpath', db, '--root', root, '--apply'])
        assert r.exit_code == 0
        assert 'MISMATCH' in r.output
        assert '1 mismatch' in r.output
        assert os.path.exists(os.path.join(root, 'NEW/Seagate Exp/p.jpg'))  # not trashed

    def test_no_survivor_outside_prefix_is_skipped(self, tmp_path, runner):
        # both copies live under the removable prefix -> nothing is safe to remove
        root, db = self._indexed(tmp_path, runner, {
            'NEW/Seagate Exp/a/p.jpg': b'dup',
            'NEW/Seagate Exp/b/p.jpg': b'dup',
        })
        r = runner.invoke(local_cli, ['dedup', '--dbpath', db, '--root', root, '--apply'])
        assert r.exit_code == 0
        assert '0 confirmed' in r.output
        import os
        assert os.path.exists(os.path.join(root, 'NEW/Seagate Exp/a/p.jpg'))
        assert os.path.exists(os.path.join(root, 'NEW/Seagate Exp/b/p.jpg'))
