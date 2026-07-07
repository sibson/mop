import sys
sys.path.insert(0, '/Users/marcs/src/mop')

from unittest.mock import MagicMock
import json
import pytest
from click.testing import CliRunner

mock_plyvel = sys.modules['plyvel']

from leveldb import FileMetaData, loads, dumps, cli as cli


def make_mock_db(sha_entries=None, meta_entries=None):
    db = MagicMock()
    sha_db = MagicMock()
    meta_db = MagicMock()

    sha_db.__iter__.return_value = sha_entries or []
    sha_db.get.return_value = b'[]'

    meta_db.__iter__.return_value = meta_entries or []

    def prefixed_side_effect(prefix):
        if prefix in (b'sha', 'sha'):
            return sha_db
        if prefix in (b'meta', 'meta'):
            return meta_db
        return MagicMock()

    db.prefixed_db.side_effect = prefixed_side_effect
    return db, sha_db, meta_db


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

    def test_file_metadata_write(self):
        ldb, sha_db, meta_db = make_mock_db()

        md = FileMetaData('b2://bucket/file.txt', 'abc123', 'file.txt')
        md.write(ldb)

        sha_db.get.assert_called_once_with(b'abc123', b'[]')
        meta_db.put.assert_called_once()
        sha_db.put.assert_called_once()

        meta_key = meta_db.put.call_args[0][0]
        meta_val = meta_db.put.call_args[0][1]
        assert meta_key == b'b2://bucket/file.txt'
        assert json.loads(meta_val) == {'uri': 'b2://bucket/file.txt', 'path': 'file.txt', 'sha1': 'abc123'}

        sha_key, sha_val = sha_db.put.call_args[0]
        assert sha_key == b'abc123'
        assert json.loads(sha_val) == ['b2://bucket/file.txt']

        ldb.get.assert_not_called()

    def test_file_metadata_write_appends_uris(self):
        ldb, sha_db, meta_db = make_mock_db()
        sha_db.get.side_effect = [
            b'[]',
            b'["b2://bucket/first.txt"]',
        ]

        md1 = FileMetaData('b2://bucket/first.txt', 'abc123', 'first.txt')
        md1.write(ldb)

        md2 = FileMetaData('b2://bucket/second.txt', 'abc123', 'second.txt')
        md2.write(ldb)

        assert sha_db.get.call_count == 2

        final_put = sha_db.put.call_args_list[-1]
        sha_val = json.loads(final_put[0][1])
        assert sorted(sha_val) == ['b2://bucket/first.txt', 'b2://bucket/second.txt']

    def test_file_metadata_skips_duplicate_uri(self):
        ldb, sha_db, meta_db = make_mock_db()
        sha_db.get.side_effect = [
            b'[]',
            b'["b2://bucket/first.txt"]',
            b'["b2://bucket/first.txt"]',
        ]

        md1 = FileMetaData('b2://bucket/first.txt', 'abc123', 'first.txt')
        md1.write(ldb)

        md_dup = FileMetaData('b2://bucket/first.txt', 'abc123', 'first.txt')
        md_dup.write(ldb)

        final_put = sha_db.put.call_args_list[-1]
        sha_val = json.loads(final_put[0][1])
        assert sha_val == ['b2://bucket/first.txt']


class TestCliCommands:

    def test_mk_command(self):
        runner = CliRunner()
        result = runner.invoke(cli, ['mk', '/tmp/test.db'])
        assert result.exit_code == 0
        mock_plyvel.DB.assert_called_once_with(
            '/tmp/test.db', create_if_missing=True, error_if_exists=True
        )

    def test_rm_command(self):
        runner = CliRunner()
        result = runner.invoke(cli, ['rm', '/tmp/test.db'])
        assert result.exit_code == 0
        mock_plyvel.destroy_db.assert_called_once_with('/tmp/test.db')

    def test_truncate_command(self):
        runner = CliRunner()
        result = runner.invoke(cli, ['truncate', '/tmp/test.db'])
        assert result.exit_code == 0
        mock_plyvel.destroy_db.assert_called_once_with('/tmp/test.db')
        mock_plyvel.DB.assert_called_once_with(
            '/tmp/test.db', create_if_missing=True, error_if_exists=True
        )

    def test_ls_command(self):
        db, sha_db, _ = make_mock_db(
            sha_entries=[
                (b'abc123', b'["b2://bucket/a.txt"]'),
                (b'def456', b'["b2://bucket/b.txt", "b2://bucket/b2.txt"]'),
            ]
        )
        mock_plyvel.DB.return_value = db

        runner = CliRunner()
        result = runner.invoke(cli, ['ls', '/tmp/test.db'])
        assert result.exit_code == 0
        assert 'abc123\tb2://bucket/a.txt' in result.output
        assert 'def456\tb2://bucket/b.txt' in result.output
        assert 'def456\tb2://bucket/b2.txt' in result.output

    def test_ls_command_with_wildcard(self):
        db, sha_db, _ = make_mock_db(
            sha_entries=[
                (b'abc123', b'["b2://bucket/a.txt", "b2://bucket/b.txt"]'),
            ]
        )
        mock_plyvel.DB.return_value = db

        runner = CliRunner()
        result = runner.invoke(cli, ['ls', '/tmp/test.db', '*a*'])
        assert result.exit_code == 0
        assert 'abc123\tb2://bucket/a.txt' in result.output
        assert 'b.txt' not in result.output

    def test_meta_command(self):
        db, _, meta_db = make_mock_db(
            meta_entries=[
                (b'u1', b'{"uri":"u1","path":"p1","sha1":"s1"}'),
                (b'u2', b'{"uri":"u2","path":"p2","sha1":"s2"}'),
            ]
        )
        mock_plyvel.DB.return_value = db

        runner = CliRunner()
        result = runner.invoke(cli, ['meta', '/tmp/test.db'])
        assert result.exit_code == 0
        assert '{"uri":"u1","path":"p1","sha1":"s1"}' in result.output
        assert '{"uri":"u2","path":"p2","sha1":"s2"}' in result.output

    def test_missing_command(self):
        target_sha_db = MagicMock()
        target_sha_db.get.return_value = None

        src_sha_db = MagicMock()
        src_sha_db.__iter__.return_value = [
            (b'abc123', b'["b2://bucket/file.txt"]'),
        ]

        mock_target_db = MagicMock()
        mock_target_db.prefixed_db.return_value = target_sha_db

        mock_src_db = MagicMock()
        mock_src_db.prefixed_db.return_value = src_sha_db

        mock_plyvel.DB.side_effect = [mock_target_db, mock_src_db]

        missing_func = cli.commands['missing'].callback

        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            missing_func('target.db', sources=['source.db'])

        output = buf.getvalue()
        assert 'source.db\tb2://bucket/file.txt' in output

        mock_plyvel.DB.side_effect = None

    def test_present_command(self):
        target_sha_db = MagicMock()
        target_sha_db.get.return_value = b'["b2://bucket/file.txt"]'

        src_sha_db = MagicMock()
        src_sha_db.__iter__.return_value = [
            (b'abc123', b'["b2://bucket/file.txt"]'),
        ]

        mock_target_db = MagicMock()
        mock_target_db.prefixed_db.side_effect = lambda p: target_sha_db if p in (b'sha', 'sha') else MagicMock()

        mock_src_db = MagicMock()
        mock_src_db.prefixed_db.side_effect = lambda p: src_sha_db if p in (b'sha', 'sha') else MagicMock()

        mock_plyvel.DB.side_effect = [mock_target_db, mock_src_db]

        runner = CliRunner()
        result = runner.invoke(cli, ['present', 'target.db', 'source.db'])
        assert result.exit_code == 0
        assert 'source.db\tb2://bucket/file.txt' in result.output

        mock_plyvel.DB.side_effect = None

    def test_merge_command(self):
        target_db, target_sha_db, target_meta_db = make_mock_db()
        target_sha_db.get.return_value = b'[]'

        src_db = MagicMock()
        src_meta_db = MagicMock()
        src_meta_db.__iter__.return_value = [
            (
                b'b2://bucket/file.txt',
                b'{"uri": "b2://bucket/file.txt", "path": "file.txt", "sha1": "abc123"}',
            ),
        ]
        src_db.prefixed_db.side_effect = lambda p: src_meta_db if p == b'meta' else MagicMock()

        mock_plyvel.DB.side_effect = [target_db, src_db]

        runner = CliRunner()
        result = runner.invoke(cli, ['merge', 'target.db', 'source.db'])
        assert result.exit_code == 0
        assert 'b2://bucket/file.txt' in result.output
        target_sha_db.put.assert_called_once()

        mock_plyvel.DB.side_effect = None

    def test_dupes_command(self):
        db, sha_db, _ = make_mock_db(
            sha_entries=[
                (b'abc', b'["u1"]'),
                (b'def', b'["u2", "u3"]'),
                (b'ghi', b'["u4"]'),
                (b'jkl', b'["u5", "u6", "u7"]'),
            ]
        )
        mock_plyvel.DB.return_value = db

        runner = CliRunner()
        result = runner.invoke(cli, ['dupes', '/tmp/test.db'])
        assert result.exit_code == 0
        assert 'def\tu2 u3' in result.output
        assert 'jkl\tu5 u6 u7' in result.output
        assert 'abc' not in result.output
        assert 'ghi' not in result.output

    def test_usage_command(self):
        db, _, meta_db = make_mock_db(
            meta_entries=[
                (
                    b'b2://bucket/folder1/file1.jpg',
                    json.dumps({
                        'uri': 'b2://bucket/folder1/file1.jpg',
                        'path': 'folder1/file1.jpg',
                        'sha1': 'a',
                        'size': 1000,
                        'fileName': 'file1.jpg',
                    }).encode(),
                ),
                (
                    b'b2://bucket/folder1/file2.jpg',
                    json.dumps({
                        'uri': 'b2://bucket/folder1/file2.jpg',
                        'path': 'folder1/file2.jpg',
                        'sha1': 'b',
                        'size': 2000,
                        'fileName': 'file2.jpg',
                    }).encode(),
                ),
                (
                    b'b2://bucket/folder2/file3.mp4',
                    json.dumps({
                        'uri': 'b2://bucket/folder2/file3.mp4',
                        'path': 'folder2/file3.mp4',
                        'sha1': 'c',
                        'size': 5000,
                        'fileName': 'file3.mp4',
                    }).encode(),
                ),
            ]
        )
        mock_plyvel.DB.return_value = db

        runner = CliRunner()
        result = runner.invoke(cli, ['usage', '/tmp/test.db'])
        assert result.exit_code == 0
        assert 'Total: 3 files, 0.0 GB' in result.output
        assert 'folder1' in result.output
        assert 'folder2' in result.output
        assert '37.5%' in result.output
        assert '62.5%' in result.output

    def test_usage_command_missing_size(self):
        db, _, meta_db = make_mock_db(
            meta_entries=[
                (
                    b'b2://bucket/nosize.txt',
                    json.dumps({
                        'uri': 'b2://bucket/nosize.txt',
                        'path': 'nosize.txt',
                        'sha1': 'd',
                    }).encode(),
                ),
            ]
        )
        mock_plyvel.DB.return_value = db

        runner = CliRunner()
        result = runner.invoke(cli, ['usage', '/tmp/test.db'])
        assert result.exit_code == 0
        assert 'Total: 0 files, 0.0 GB' in result.output
        assert 'without size data' in result.output

    def test_usage_csv_flag(self):
        db, _, meta_db = make_mock_db(
            meta_entries=[
                (
                    b'b2://bucket/folder1/file1.jpg',
                    json.dumps({
                        'uri': 'b2://bucket/folder1/file1.jpg',
                        'path': 'folder1/file1.jpg',
                        'sha1': 'a',
                        'size': 1000,
                        'fileName': 'file1.jpg',
                    }).encode(),
                ),
                (
                    b'b2://bucket/folder1/file2.jpg',
                    json.dumps({
                        'uri': 'b2://bucket/folder1/file2.jpg',
                        'path': 'folder1/file2.jpg',
                        'sha1': 'b',
                        'size': 2000,
                        'fileName': 'file2.jpg',
                    }).encode(),
                ),
                (
                    b'b2://bucket/folder2/file3.mp4',
                    json.dumps({
                        'uri': 'b2://bucket/folder2/file3.mp4',
                        'path': 'folder2/file3.mp4',
                        'sha1': 'c',
                        'size': 5000,
                        'fileName': 'file3.mp4',
                    }).encode(),
                ),
            ]
        )
        mock_plyvel.DB.return_value = db

        runner = CliRunner()
        result = runner.invoke(cli, ['usage', '--csv', '/tmp/test.db'])
        assert result.exit_code == 0
        assert 'category,name,size_bytes' in result.output
        assert 'summary,total_files,3' in result.output
        assert 'summary,total_size,8000' in result.output
        assert 'folder,folder1,3000' in result.output
        assert 'folder,folder2,5000' in result.output
