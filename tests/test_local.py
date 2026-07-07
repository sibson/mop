import sys
sys.path.insert(0, '/Users/marcs/src/mop')

from unittest.mock import MagicMock, patch
import pytest
from click.testing import CliRunner

mock_plyvel = sys.modules['plyvel']

from local import cli as local_cli


@pytest.fixture(autouse=True)
def reset_plyvel():
    mock_plyvel.DB.reset_mock()
    mock_plyvel.DB.return_value = MagicMock()


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

    def test_index_command(self):
        mock_ldb = MagicMock()
        mock_sha_db = MagicMock()
        mock_sha_db.get.return_value = b'[]'
        mock_meta_db = MagicMock()
        mock_ldb.prefixed_db.side_effect = lambda p: {
            b'sha': mock_sha_db,
            b'meta': mock_meta_db,
        }.get(p, MagicMock())
        mock_plyvel.DB.return_value = mock_ldb

        with patch('local.os.walk') as mock_walk:
            mock_walk.return_value = [
                ('/base', [], ['a.txt', 'b.jpg']),
            ]
            with patch('local.sha1') as mock_sha1:
                mock_sha1.side_effect = ['sha_a', 'sha_b']

                runner = CliRunner()
                result = runner.invoke(local_cli, ['index', '/base'])

                assert result.exit_code == 0
                mock_plyvel.DB.assert_called_once_with('.mop.db', create_if_missing=True)
                assert 'sha_a a.txt' in result.output
                assert 'sha_b b.jpg' in result.output
                assert mock_sha_db.put.call_count == 2
                assert mock_meta_db.put.call_count == 2

    def test_index_custom_dbpath(self):
        mock_ldb = MagicMock()
        mock_sha_db = MagicMock()
        mock_sha_db.get.return_value = b'[]'
        mock_meta_db = MagicMock()
        mock_ldb.prefixed_db.side_effect = lambda p: {
            b'sha': mock_sha_db,
            b'meta': mock_meta_db,
        }.get(p, MagicMock())
        mock_plyvel.DB.return_value = mock_ldb

        with patch('local.os.walk') as mock_walk:
            mock_walk.return_value = [
                ('/pics', [], ['photo.jpg']),
            ]
            with patch('local.sha1') as mock_sha1:
                mock_sha1.return_value = 'deadbeef'

                runner = CliRunner()
                result = runner.invoke(local_cli, [
                    'index', '--dbpath', '/tmp/custom.db', '/pics',
                ])

                assert result.exit_code == 0
                mock_plyvel.DB.assert_called_once_with('/tmp/custom.db', create_if_missing=True)

    def test_index_handles_nested_dirs(self):
        mock_ldb = MagicMock()
        mock_sha_db = MagicMock()
        mock_sha_db.get.return_value = b'[]'
        mock_meta_db = MagicMock()
        mock_ldb.prefixed_db.side_effect = lambda p: {
            b'sha': mock_sha_db,
            b'meta': mock_meta_db,
        }.get(p, MagicMock())
        mock_plyvel.DB.return_value = mock_ldb

        with patch('local.os.walk') as mock_walk:
            mock_walk.return_value = [
                ('/base', ['sub'], []),
                ('/base/sub', [], ['deep.txt']),
            ]
            with patch('local.sha1') as mock_sha1:
                mock_sha1.return_value = 'nested_sha'

                runner = CliRunner()
                result = runner.invoke(local_cli, ['index', '/base'])

                assert result.exit_code == 0
                assert 'nested_sha deep.txt' in result.output
                assert mock_sha_db.put.call_count == 1
