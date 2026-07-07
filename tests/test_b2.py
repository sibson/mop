import sys
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner


class FileAlreadyHidden(Exception):
    pass

class FileNotPresent(Exception):
    pass

b2sdk_v1_exception = MagicMock()
b2sdk_v1_exception.FileAlreadyHidden = FileAlreadyHidden
b2sdk_v1_exception.FileNotPresent = FileNotPresent
sys.modules['b2sdk.v1.exception'] = b2sdk_v1_exception

b2sdk_v1 = MagicMock()
b2sdk_v1.SqliteAccountInfo = MagicMock()
b2sdk_v1.B2Api = MagicMock()
sys.modules['b2sdk.v1'] = b2sdk_v1

sys.modules['b2sdk'] = MagicMock()
sys.modules['b2sdk.exception'] = MagicMock()

import store
from b2 import cli as b2_cli


def make_file_info(name, sha1='abc123deadbeef', size=1000, file_id='f123'):
    fi = MagicMock()
    fi.file_name = name
    fi.content_sha1 = sha1
    fi.size = size
    fi.id_ = file_id
    fi.file_info = {}
    return fi


@pytest.fixture(autouse=True)
def auto_reset():
    # b2sdk is fully mocked; the store side runs against real sqlite.
    b2sdk_v1.SqliteAccountInfo.reset_mock()
    b2sdk_v1.B2Api.reset_mock()

    mock_b2 = MagicMock()
    mock_bucket = MagicMock()
    mock_b2.get_bucket_by_name.return_value = mock_bucket
    b2sdk_v1.B2Api.return_value = mock_b2

    return mock_b2, mock_bucket


class TestLogin:

    def test_login_command(self):
        with patch('b2.getpass') as mock_getpass:
            mock_getpass.side_effect = ['keyid_here', 'appkey_here']

            runner = CliRunner()
            result = runner.invoke(b2_cli, ['login'])

            assert result.exit_code == 0
            mock_b2 = b2sdk_v1.B2Api.return_value
            mock_b2.authorize_account.assert_called_once_with(
                'production', 'keyid_here', 'appkey_here'
            )


class TestSha1sum:

    def test_sha1sum_command(self):
        mock_b2 = b2sdk_v1.B2Api.return_value
        mock_bucket = mock_b2.get_bucket_by_name.return_value

        fi1 = make_file_info('folder/a.txt', 'deadbeef')
        fi2 = make_file_info('folder/b.txt', 'cafebabe')
        mock_bucket.ls.return_value = [(fi1, 'folder'), (fi2, 'folder')]

        runner = CliRunner()
        result = runner.invoke(b2_cli, ['sha1sum', 'my-bucket'])

        assert result.exit_code == 0
        assert 'deadbeef folder/a.txt' in result.output
        assert 'cafebabe folder/b.txt' in result.output


class TestIndex:

    def test_index_command(self, tmp_path):
        mock_b2 = b2sdk_v1.B2Api.return_value
        mock_bucket = mock_b2.get_bucket_by_name.return_value

        fi1 = make_file_info('folder/a.jpg', 'sha1aaaa', size=1048576)
        fi2 = make_file_info('folder/b.jpg', 'sha1bbbb', size=2097152)
        mock_bucket.ls.return_value = [(fi1, 'folder'), (fi2, 'folder')]

        dbpath = str(tmp_path / 'b2.db')
        runner = CliRunner()
        result = runner.invoke(b2_cli, ['index', '--dbpath', dbpath, 'my-bucket'])

        assert result.exit_code == 0
        assert 'Total 2 files' in result.output
        assert '3.0MB' in result.output

        # Rows landed in a real sqlite db with size lifted to its column and the
        # b2 metadata in extra.
        conn = store.connect(dbpath)
        rows = dict(
            (uri, (sha1, size, extra))
            for uri, sha1, size, extra
            in conn.execute('SELECT uri, sha1, size, extra FROM files'))
        conn.close()
        assert set(rows) == {
            'b2://my-bucket/folder/a.jpg',
            'b2://my-bucket/folder/b.jpg',
        }
        sha1, size, extra = rows['b2://my-bucket/folder/a.jpg']
        assert sha1 == 'sha1aaaa'
        assert size == 1048576
        extra = store.loads(extra)
        assert extra['bucket'] == 'my-bucket'
        assert extra['fileName'] == 'folder/a.jpg'
        assert extra['fileId'] == 'f123'

    def test_index_wipes_existing_db(self, tmp_path):
        # index removes and rebuilds the db; stale rows must not survive.
        dbpath = str(tmp_path / 'b2wipe.db')
        conn = store.connect(dbpath, create=True)
        store.FileMetaData('b2://my-bucket/stale.txt', 'oldsha', 'stale.txt').write(conn)
        conn.commit()
        conn.close()

        mock_b2 = b2sdk_v1.B2Api.return_value
        mock_bucket = mock_b2.get_bucket_by_name.return_value
        fi1 = make_file_info('fresh.jpg', 'freshsha', size=500)
        mock_bucket.ls.return_value = [(fi1, '')]

        runner = CliRunner()
        result = runner.invoke(b2_cli, ['index', '--dbpath', dbpath, 'my-bucket'])
        assert result.exit_code == 0

        conn = store.connect(dbpath)
        uris = [r[0] for r in conn.execute('SELECT uri FROM files')]
        conn.close()
        assert uris == ['b2://my-bucket/fresh.jpg']

    def test_index_large_file_sha1_fallback(self, tmp_path):
        mock_b2 = b2sdk_v1.B2Api.return_value
        mock_bucket = mock_b2.get_bucket_by_name.return_value

        fi1 = make_file_info('large.bin', 'none', size=1073741824)
        fi1.content_sha1 = 'none'
        fi1.file_info = {'large_file_sha1': 'largefile_sha1_value'}
        mock_bucket.ls.return_value = [(fi1, '')]

        dbpath = str(tmp_path / 'b2large.db')
        runner = CliRunner()
        result = runner.invoke(b2_cli, ['index', '--dbpath', dbpath, 'my-bucket'])

        assert result.exit_code == 0
        assert 'largefile_sha1_value' in result.output
        conn = store.connect(dbpath)
        sha1 = conn.execute('SELECT sha1 FROM files').fetchone()[0]
        conn.close()
        assert sha1 == 'largefile_sha1_value'

    def test_index_with_options(self, tmp_path):
        mock_b2 = b2sdk_v1.B2Api.return_value
        mock_bucket = mock_b2.get_bucket_by_name.return_value

        fi1 = make_file_info('folder/a.jpg', 'sha1aaaa', size=500)
        mock_bucket.ls.return_value = [(fi1, 'folder')]

        dbpath = str(tmp_path / 'custom.db')
        runner = CliRunner()
        result = runner.invoke(b2_cli, [
            'index', '--dbpath', dbpath, '--folder', 'sub',
            '--recursive', '--max-count', '50', 'my-bucket',
        ])

        assert result.exit_code == 0
        mock_bucket.ls.assert_called_once_with(
            folder_to_list='sub', recursive=True, fetch_count=50
        )
        import os
        assert os.path.exists(dbpath)


class TestHide:

    def test_hide_command(self):
        mock_b2 = b2sdk_v1.B2Api.return_value
        mock_bucket = mock_b2.get_bucket_by_name.return_value

        runner = CliRunner()
        result = runner.invoke(
            b2_cli,
            ['hide', 'my-bucket', '-'],
            input='b2://my-bucket/path/a.txt\nb2://my-bucket/other/b.txt\n',
        )

        assert result.exit_code == 0
        assert mock_bucket.hide_file.call_count == 2
        mock_bucket.hide_file.assert_any_call('path/a.txt')
        mock_bucket.hide_file.assert_any_call('other/b.txt')
        assert 'path/a.txt hidden' in result.output
        assert 'other/b.txt hidden' in result.output
        assert 'Total: 2 hidden, 0 not found, 0 already hidden' in result.output

    def test_hide_does_not_strip_non_b2_paths(self):
        mock_b2 = b2sdk_v1.B2Api.return_value
        mock_bucket = mock_b2.get_bucket_by_name.return_value

        runner = CliRunner()
        result = runner.invoke(
            b2_cli,
            ['hide', 'my-bucket', '-'],
            input='path/a.txt\nrando/b.txt\n',
        )

        assert result.exit_code == 0
        mock_bucket.hide_file.assert_any_call('path/a.txt')
        mock_bucket.hide_file.assert_any_call('rando/b.txt')

    def test_hide_file_already_hidden(self):
        mock_b2 = b2sdk_v1.B2Api.return_value
        mock_bucket = mock_b2.get_bucket_by_name.return_value
        mock_bucket.hide_file.side_effect = FileAlreadyHidden('x')

        runner = CliRunner()
        result = runner.invoke(
            b2_cli,
            ['hide', 'my-bucket', '-'],
            input='b2://my-bucket/path/a.txt\n',
        )

        assert result.exit_code == 0
        assert 'hidden' in result.output

    def test_hide_file_not_present(self):
        mock_b2 = b2sdk_v1.B2Api.return_value
        mock_bucket = mock_b2.get_bucket_by_name.return_value
        mock_bucket.hide_file.side_effect = FileNotPresent('x')

        runner = CliRunner()
        result = runner.invoke(
            b2_cli,
            ['hide', 'my-bucket', '-'],
            input='b2://my-bucket/path/a.txt\n',
        )

        assert result.exit_code == 0
        assert 'NOT FOUND' in result.output

    def test_hide_dry_run_does_not_call_hide_file(self):
        mock_b2 = b2sdk_v1.B2Api.return_value
        mock_bucket = mock_b2.get_bucket_by_name.return_value

        runner = CliRunner()
        result = runner.invoke(
            b2_cli,
            ['hide', '--dry-run', 'my-bucket', '-'],
            input='b2://my-bucket/path/a.txt\nb2://my-bucket/other/b.txt\n',
        )

        assert result.exit_code == 0
        mock_bucket.hide_file.assert_not_called()
        assert 'path/a.txt would hide' in result.output
        assert 'other/b.txt would hide' in result.output
        assert 'Total:' not in result.output

    def test_hide_log_writes_hidden_paths(self, tmp_path):
        mock_b2 = b2sdk_v1.B2Api.return_value
        mock_bucket = mock_b2.get_bucket_by_name.return_value
        mock_bucket.hide_file.side_effect = [None, FileNotPresent('x')]

        log_path = tmp_path / 'hidden.log'

        runner = CliRunner()
        result = runner.invoke(
            b2_cli,
            ['hide', '--log', str(log_path), 'my-bucket', '-'],
            input='b2://my-bucket/path/a.txt\nb2://my-bucket/missing/b.txt\n',
        )

        assert result.exit_code == 0
        log_contents = log_path.read_text()
        assert log_contents == 'path/a.txt\thidden\n'

    def test_hide_prints_count_summary(self):
        mock_b2 = b2sdk_v1.B2Api.return_value
        mock_bucket = mock_b2.get_bucket_by_name.return_value
        mock_bucket.hide_file.side_effect = [
            None, FileAlreadyHidden('x'), FileNotPresent('x'),
        ]

        runner = CliRunner()
        result = runner.invoke(
            b2_cli,
            ['hide', 'my-bucket', '-'],
            input='a.txt\nb.txt\nc.txt\n',
        )

        assert result.exit_code == 0
        assert 'Total: 1 hidden, 1 not found, 1 already hidden' in result.output
