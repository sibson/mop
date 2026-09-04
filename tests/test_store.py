import json

import pytest
from click.testing import CliRunner

import store
from store import FileMetaData, loads, dumps, cli


@pytest.fixture
def runner():
    return CliRunner()


class TestFileMetaData:

    def test_file_metadata_create(self):
        md = FileMetaData(
            'b2://bucket/file.txt',
            'abc123def',
            'folder/file.txt',
            size=1024,
            mtime=1700000000,
        )
        assert md.uri == 'b2://bucket/file.txt'
        assert md.sha1 == 'abc123def'
        assert md.path == 'folder/file.txt'
        assert md.size == 1024
        assert md.mtime == 1700000000
        assert 'size' in md.kwkeys
        assert 'mtime' in md.kwkeys

    def test_file_metadata_skips_dunder_kwargs(self):
        md = FileMetaData('u', 's', 'p', **{'__class__': 'FileMetaData'})
        assert '__class__' not in md.kwkeys

    def test_file_metadata_json(self):
        md = FileMetaData('b2://bucket/file.txt', 'abc123', 'file.txt', size=100)
        d = md.__json__()
        assert d == {'uri': 'b2://bucket/file.txt', 'path': 'file.txt', 'sha1': 'abc123', 'size': 100}

    def test_file_metadata_json_no_extra(self):
        md = FileMetaData('b2://bucket/f.mp4', 'deadbeef', 'f.mp4')
        d = md.__json__()
        assert d == {'uri': 'b2://bucket/f.mp4', 'path': 'f.mp4', 'sha1': 'deadbeef'}

    def test_file_metadata_write_row(self, db_path):
        # size lifts into its own column; no other kwargs -> extra is NULL.
        conn = store.connect(db_path)
        FileMetaData('b2://bucket/file.txt', 'abc123', 'file.txt').write(conn)
        conn.commit()
        row = conn.execute(
            'SELECT uri, sha1, path, size, extra FROM files WHERE uri=?',
            ('b2://bucket/file.txt',)).fetchone()
        conn.close()
        assert row == ('b2://bucket/file.txt', 'abc123', 'file.txt', None, None)

    def test_file_metadata_write_size_column(self, db_path):
        conn = store.connect(db_path)
        FileMetaData('u', 's', 'p', size=1234).write(conn)
        conn.commit()
        row = conn.execute('SELECT size, extra FROM files WHERE uri=?', ('u',)).fetchone()
        conn.close()
        # size lifted to its own column; extra NULL since no other kwargs.
        assert row == (1234, None)

    def test_file_metadata_write_extra_json(self, db_path):
        conn = store.connect(db_path)
        FileMetaData('u', 's', 'p', size=10, fileName='f.txt', bucket='b').write(conn)
        conn.commit()
        size, extra = conn.execute(
            'SELECT size, extra FROM files WHERE uri=?', ('u',)).fetchone()
        conn.close()
        assert size == 10
        assert loads(extra) == {'fileName': 'f.txt', 'bucket': 'b'}

    def test_file_metadata_write_upsert(self, db_path):
        # INSERT OR REPLACE on uri PK: second write overwrites the first.
        conn = store.connect(db_path)
        FileMetaData('u', 'sha_old', 'p', size=1).write(conn)
        FileMetaData('u', 'sha_new', 'p2', size=2).write(conn)
        conn.commit()
        rows = conn.execute('SELECT sha1, path, size FROM files WHERE uri=?', ('u',)).fetchall()
        conn.close()
        assert rows == [('sha_new', 'p2', 2)]

    def test_file_metadata_write_no_commit(self, db_path):
        # .write must NOT commit; an uncommitted row is not visible to a
        # separate connection.
        conn = store.connect(db_path)
        FileMetaData('u', 's', 'p').write(conn)
        other = store.connect(db_path)
        assert other.execute('SELECT COUNT(*) FROM files').fetchone()[0] == 0
        other.close()
        conn.commit()
        assert other is not conn
        conn.close()


class TestCliCommands:

    def test_mk_command(self, tmp_path, runner):
        path = str(tmp_path / 'new.db')
        result = runner.invoke(cli, ['mk', path])
        assert result.exit_code == 0
        # A usable db with the files table now exists.
        conn = store.connect(path)
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='files'").fetchone()
        conn.close()

    def test_mk_command_errors_if_exists(self, db_path, runner):
        result = runner.invoke(cli, ['mk', db_path])
        assert result.exit_code != 0
        assert 'already exists' in result.output

    def test_rm_command(self, seed, runner):
        import os
        path = seed('rm.db', [('u', 's', 'p')])
        assert os.path.exists(path)
        result = runner.invoke(cli, ['rm', path])
        assert result.exit_code == 0
        assert not os.path.exists(path)
        assert not os.path.exists(path + '-wal')
        assert not os.path.exists(path + '-shm')

    def test_truncate_command(self, seed, runner):
        path = seed('trunc.db', [('u1', 's1', 'p1'), ('u2', 's2', 'p2')])
        result = runner.invoke(cli, ['truncate', path])
        assert result.exit_code == 0
        conn = store.connect(path)
        assert conn.execute('SELECT COUNT(*) FROM files').fetchone()[0] == 0
        conn.close()

    def test_ls_command(self, seed, runner):
        path = seed('ls.db', [
            ('b2://bucket/a.txt', 'abc123', 'a.txt'),
            ('b2://bucket/b.txt', 'def456', 'b.txt'),
            ('b2://bucket/b2.txt', 'def456', 'b2.txt'),
        ])
        result = runner.invoke(cli, ['ls', path])
        assert result.exit_code == 0
        assert 'abc123\tb2://bucket/a.txt' in result.output
        assert 'def456\tb2://bucket/b.txt' in result.output
        assert 'def456\tb2://bucket/b2.txt' in result.output

    def test_ls_command_ordered_by_sha1(self, seed, runner):
        path = seed('lso.db', [
            ('u_z', 'zzz', 'p'),
            ('u_a', 'aaa', 'p'),
            ('u_m', 'mmm', 'p'),
        ])
        result = runner.invoke(cli, ['ls', path])
        assert result.exit_code == 0
        shas = [line.split('\t')[0] for line in result.output.splitlines() if line]
        assert shas == ['aaa', 'mmm', 'zzz']

    def test_ls_command_with_wildcard(self, seed, runner):
        path = seed('lsw.db', [
            ('b2://bucket/a.txt', 'abc123', 'a.txt'),
            ('b2://bucket/b.txt', 'abc123', 'b.txt'),
        ])
        result = runner.invoke(cli, ['ls', path, '*a*'])
        assert result.exit_code == 0
        assert 'abc123\tb2://bucket/a.txt' in result.output
        assert 'b.txt' not in result.output

    def test_ls_command_with_prefix(self, seed, runner):
        path = seed('lsp.db', [
            ('file:///a/NEW/2010/x.jpg', 'abc', 'x.jpg'),
            ('file:///a/NEW/Olympus/y.jpg', 'def', 'y.jpg'),
        ])
        result = runner.invoke(cli, ['ls', path, '--prefix', 'file:///a/NEW/2010/'])
        assert result.exit_code == 0
        assert 'abc\tfile:///a/NEW/2010/x.jpg' in result.output
        assert 'Olympus' not in result.output

    def test_ls_command_prefix_escapes_like_metacharacters(self, seed, runner):
        # A literal '_' in the prefix must not act as a SQL LIKE wildcard
        # (which would also match 'USBXDrive').
        path = seed('lspe.db', [
            ('file:///a/USB_Drive/x.jpg', 'abc', 'x.jpg'),
            ('file:///a/USBXDrive/y.jpg', 'def', 'y.jpg'),
        ])
        result = runner.invoke(cli, ['ls', path, '--prefix', 'file:///a/USB_Drive/'])
        assert result.exit_code == 0
        assert 'USB_Drive' in result.output
        assert 'USBXDrive' not in result.output

    def test_meta_command(self, seed, runner):
        path = seed('meta.db', [
            ('u1', 's1', 'p1'),
            ('u2', 's2', 'p2', {'size': 42, 'fileName': 'f2'}),
        ])
        result = runner.invoke(cli, ['meta', path])
        assert result.exit_code == 0
        lines = [l for l in result.output.splitlines() if l]
        parsed = {json.loads(l)['uri']: json.loads(l) for l in lines}
        assert parsed['u1'] == {'uri': 'u1', 'path': 'p1', 'sha1': 's1'}
        assert parsed['u2'] == {'uri': 'u2', 'path': 'p2', 'sha1': 's2', 'fileName': 'f2', 'size': 42}

    def test_dupes_command(self, seed, runner):
        path = seed('dupes.db', [
            ('u1', 'abc', 'p'),
            ('u2', 'def', 'p'),
            ('u3', 'def', 'p'),
            ('u4', 'ghi', 'p'),
            ('u5', 'jkl', 'p'),
            ('u6', 'jkl', 'p'),
            ('u7', 'jkl', 'p'),
        ])
        result = runner.invoke(cli, ['dupes', path])
        assert result.exit_code == 0
        # Build sha1 -> set(uris) from output; within-group order is db-order
        # dependent, so compare as sets.
        groups = {}
        for line in result.output.splitlines():
            if not line:
                continue
            sha1, uris = line.split('\t')
            groups[sha1] = set(uris.split(' '))
        assert groups == {'def': {'u2', 'u3'}, 'jkl': {'u5', 'u6', 'u7'}}
        # singletons excluded
        assert 'abc' not in groups
        assert 'ghi' not in groups

    def test_usage_command(self, seed, runner):
        path = seed('usage.db', [
            ('b2://bucket/folder1/file1.jpg', 'a', 'folder1/file1.jpg', {'size': 1000, 'fileName': 'file1.jpg'}),
            ('b2://bucket/folder1/file2.jpg', 'b', 'folder1/file2.jpg', {'size': 2000, 'fileName': 'file2.jpg'}),
            ('b2://bucket/folder2/file3.mp4', 'c', 'folder2/file3.mp4', {'size': 5000, 'fileName': 'file3.mp4'}),
        ])
        result = runner.invoke(cli, ['usage', path])
        assert result.exit_code == 0
        assert 'Total: 3 files, 0.0 GB' in result.output
        assert 'folder1' in result.output
        assert 'folder2' in result.output
        assert '37.5%' in result.output
        assert '62.5%' in result.output

    def test_usage_command_missing_size(self, seed, runner):
        path = seed('usage_ns.db', [
            ('b2://bucket/nosize.txt', 'd', 'nosize.txt'),
        ])
        result = runner.invoke(cli, ['usage', path])
        assert result.exit_code == 0
        assert 'Total: 0 files, 0.0 GB' in result.output
        assert 'without size data' in result.output

    def test_usage_csv_flag(self, seed, runner):
        path = seed('usage_csv.db', [
            ('b2://bucket/folder1/file1.jpg', 'a', 'folder1/file1.jpg', {'size': 1000, 'fileName': 'file1.jpg'}),
            ('b2://bucket/folder1/file2.jpg', 'b', 'folder1/file2.jpg', {'size': 2000, 'fileName': 'file2.jpg'}),
            ('b2://bucket/folder2/file3.mp4', 'c', 'folder2/file3.mp4', {'size': 5000, 'fileName': 'file3.mp4'}),
        ])
        result = runner.invoke(cli, ['usage', '--csv', path])
        assert result.exit_code == 0
        assert 'category,name,size_bytes' in result.output
        assert 'summary,total_files,3' in result.output
        assert 'summary,total_size,8000' in result.output
        assert 'folder,folder1,3000' in result.output
        assert 'folder,folder2,5000' in result.output


class TestCrossDbCommands:
    """missing / present / merge exercise the ATTACH path across two real dbs."""

    def test_missing_command(self, seed, runner):
        # target has sha 'shared'; source has 'shared' and 'onlysrc'.
        target = seed('t_missing.db', [('t1', 'shared', 'p')])
        source = seed('s_missing.db', [
            ('b2://bucket/present.txt', 'shared', 'present.txt'),
            ('b2://bucket/absent.txt', 'onlysrc', 'absent.txt'),
        ])
        result = runner.invoke(cli, ['missing', target, source])
        assert result.exit_code == 0
        # Only the sha not present in target is reported, tagged with src name.
        assert f'{source}\tb2://bucket/absent.txt' in result.output
        assert 'present.txt' not in result.output

    def test_missing_multiple_sources(self, seed, runner):
        target = seed('t_missing2.db', [('t1', 'have', 'p')])
        src_a = seed('sa.db', [('a1', 'gone_a', 'pa')])
        src_b = seed('sb.db', [('b1', 'gone_b', 'pb'), ('b2', 'have', 'pb2')])
        result = runner.invoke(cli, ['missing', target, src_a, src_b])
        assert result.exit_code == 0
        assert f'{src_a}\ta1' in result.output
        assert f'{src_b}\tb1' in result.output
        # 'have' sha present in target -> b2 not reported
        assert f'{src_b}\tb2' not in result.output

    def test_present_command(self, seed, runner):
        target = seed('t_present.db', [('t1', 'shared', 'p')])
        source = seed('s_present.db', [
            ('b2://bucket/file.txt', 'shared', 'file.txt'),
            ('b2://bucket/other.txt', 'onlysrc', 'other.txt'),
        ])
        result = runner.invoke(cli, ['present', target, source])
        assert result.exit_code == 0
        assert f'{source}\tb2://bucket/file.txt' in result.output
        assert 'other.txt' not in result.output

    def test_missing_command_with_prefix(self, seed, runner):
        # Two sha's are absent from target; --prefix restricts which of the
        # source's uris are even considered, independent of the sha1 match.
        target = seed('t_missing_p.db', [('t1', 'have', 'p')])
        source = seed('s_missing_p.db', [
            ('file:///NEW/2010/a.jpg', 'gone_a', 'a.jpg'),
            ('file:///NEW/Olympus/b.jpg', 'gone_b', 'b.jpg'),
        ])
        result = runner.invoke(cli, ['missing', target, source, '--prefix', 'file:///NEW/2010/'])
        assert result.exit_code == 0
        assert f'{source}\tfile:///NEW/2010/a.jpg' in result.output
        assert 'Olympus' not in result.output

    def test_present_command_with_prefix(self, seed, runner):
        target = seed('t_present_p.db', [('t1', 'shared', 'p')])
        source = seed('s_present_p.db', [
            ('file:///NEW/2010/a.jpg', 'shared', 'a.jpg'),
            ('file:///NEW/Olympus/b.jpg', 'shared', 'b.jpg'),
        ])
        result = runner.invoke(cli, ['present', target, source, '--prefix', 'file:///NEW/2010/'])
        assert result.exit_code == 0
        assert f'{source}\tfile:///NEW/2010/a.jpg' in result.output
        assert 'Olympus' not in result.output

    def test_merge_command(self, seed, runner):
        target = seed('t_merge.db', [('t_existing', 'sx', 'px')])
        source = seed('s_merge.db', [
            ('b2://bucket/file.txt', 'abc123', 'file.txt', {'size': 5}),
        ])
        result = runner.invoke(cli, ['merge', target, source])
        assert result.exit_code == 0
        assert 'b2://bucket/file.txt' in result.output
        # merged row is now in target
        conn = store.connect(target)
        rows = dict(conn.execute('SELECT uri, sha1 FROM files'))
        conn.close()
        assert rows == {'t_existing': 'sx', 'b2://bucket/file.txt': 'abc123'}

    def test_merge_dedups_on_uri(self, seed, runner):
        # target already has uri 'dup'; INSERT OR IGNORE keeps target's row.
        target = seed('t_merge2.db', [('dup', 'target_sha', 'p_target')])
        source = seed('s_merge2.db', [
            ('dup', 'source_sha', 'p_source'),
            ('fresh', 'fresh_sha', 'p_fresh'),
        ])
        result = runner.invoke(cli, ['merge', target, source])
        assert result.exit_code == 0
        conn = store.connect(target)
        rows = dict(conn.execute('SELECT uri, sha1 FROM files'))
        conn.close()
        assert rows == {'dup': 'target_sha', 'fresh': 'fresh_sha'}

    def test_merge_warns_on_sha1_conflict(self, seed):
        # a uri present in both with a DIFFERENT sha1 -> target kept, warned on stderr;
        # a uri present in both with the SAME sha1 -> no warning.
        target = seed('t_conf.db', [('dup', 'target_sha', 'p'), ('agree', 'same', 'p')])
        source = seed('s_conf.db', [
            ('dup', 'source_sha', 'p'),
            ('agree', 'same', 'p'),
            ('fresh', 'fresh_sha', 'p'),
        ])
        result = CliRunner().invoke(cli, ['merge', target, source])
        assert result.exit_code == 0
        # every source uri is still echoed on stdout
        for uri in ('dup', 'agree', 'fresh'):
            assert uri in result.output
        # the conflict notice goes to stderr, and ONLY the conflicting uri is flagged
        assert ('conflict dup: keeping target sha1 target_sha, '
                'ignoring source source_sha') in result.stderr
        assert 'conflict agree' not in result.stderr  # matching sha1 not flagged
        assert 'conflict fresh' not in result.stderr  # new uri is not a conflict
