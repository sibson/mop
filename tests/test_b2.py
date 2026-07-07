import sys
sys.path.insert(0, '/Users/marcs/src/mop')

from unittest.mock import MagicMock, patch
import pytest
from click.testing import CliRunner

mock_plyvel = sys.modules['plyvel']


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
    mock_plyvel.DB.reset_mock()
    mock_plyvel.destroy_db.reset_mock()
    mock_plyvel.DB.side_effect = None
    mock_plyvel.DB.return_value = MagicMock()

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

    def test_index_command(self):
        mock_b2 = b2sdk_v1.B2Api.return_value
        mock_bucket = mock_b2.get_bucket_by_name.return_value

        fi1 = make_file_info('folder/a.jpg', 'sha1aaaa', size=1048576)
        fi2 = make_file_info('folder/b.jpg', 'sha1bbbb', size=2097152)
        mock_bucket.ls.return_value = [(fi1, 'folder'), (fi2, 'folder')]

        mock_ldb = MagicMock()
        mock_sha_db = MagicMock()
        mock_sha_db.get.return_value = b'[]'
        mock_ldb.prefixed_db.return_value = mock_sha_db
        mock_plyvel.DB.return_value = mock_ldb

        runner = CliRunner()
        result = runner.invoke(b2_cli, ['index', 'my-bucket'])

        assert result.exit_code == 0
        mock_plyvel.destroy_db.assert_called_once_with('.b2.db')
        mock_plyvel.DB.assert_called_with('.b2.db', create_if_missing=True)
        assert 'Total 2 files' in result.output
        assert '3.0MB' in result.output

    def test_index_large_file_sha1_fallback(self):
        mock_b2 = b2sdk_v1.B2Api.return_value
        mock_bucket = mock_b2.get_bucket_by_name.return_value

        fi1 = make_file_info('large.bin', 'none', size=1073741824)
        fi1.content_sha1 = 'none'
        fi1.file_info = {'large_file_sha1': 'largefile_sha1_value'}

        mock_bucket.ls.return_value = [(fi1, '')]

        mock_ldb = MagicMock()
        mock_sha_db = MagicMock()
        mock_sha_db.get.return_value = b'[]'
        mock_ldb.prefixed_db.return_value = mock_sha_db
        mock_plyvel.DB.return_value = mock_ldb

        runner = CliRunner()
        result = runner.invoke(b2_cli, ['index', 'my-bucket'])

        assert result.exit_code == 0
        assert 'largefile_sha1_value' in result.output

    def test_index_with_options(self):
        mock_b2 = b2sdk_v1.B2Api.return_value
        mock_bucket = mock_b2.get_bucket_by_name.return_value

        fi1 = make_file_info('folder/a.jpg', 'sha1aaaa', size=500)
        mock_bucket.ls.return_value = [(fi1, 'folder')]

        mock_ldb = MagicMock()
        mock_sha_db = MagicMock()
        mock_sha_db.get.return_value = b'[]'
        mock_ldb.prefixed_db.return_value = mock_sha_db
        mock_plyvel.DB.return_value = mock_ldb

        runner = CliRunner()
        result = runner.invoke(b2_cli, [
            'index', '--dbpath', '/tmp/custom.db', '--folder', 'sub',
            '--recursive', '--max-count', '50', 'my-bucket',
        ])

        assert result.exit_code == 0
        mock_bucket.ls.assert_called_once_with(
            folder_to_list='sub', recursive=True, fetch_count=50
        )
        mock_plyvel.destroy_db.assert_called_once_with('/tmp/custom.db')
        mock_plyvel.DB.assert_called_with('/tmp/custom.db', create_if_missing=True)


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
