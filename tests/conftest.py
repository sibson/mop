import os
import sys

import pytest

# Make the project modules (store, local, b2, mopcli) importable without a
# hardcoded absolute path. conftest.py lives in tests/, so the project root is
# its parent directory.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import store


@pytest.fixture
def db_path(tmp_path):
    """Path (str) to a fresh, empty sqlite db file created with the schema."""
    path = str(tmp_path / 'test.db')
    conn = store.connect(path, create=True)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def seed(tmp_path):
    """Return a helper that seeds rows into a db and commits.

    Usage:
        path = seed('a.db', [
            ('uri', 'sha1', 'path'),                 # positional
            ('uri', 'sha1', 'path', {'size': 100}),  # + kwargs dict
        ])

    The db is created if it does not exist. Returns the db path (str).
    """
    def _seed(name, rows):
        path = str(tmp_path / name)
        conn = store.connect(path, create=True)
        for row in rows:
            uri, sha1, p = row[0], row[1], row[2]
            kwargs = row[3] if len(row) > 3 else {}
            store.FileMetaData(uri, sha1, p, **kwargs).write(conn)
        conn.commit()
        conn.close()
        return path

    return _seed
