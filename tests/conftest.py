import sys
from unittest.mock import MagicMock
import pytest

mock_plyvel = MagicMock()
sys.modules['plyvel'] = mock_plyvel


@pytest.fixture(autouse=True)
def reset_plyvel():
    mock_plyvel.DB.reset_mock()
    mock_plyvel.destroy_db.reset_mock()
    mock_plyvel.DB.side_effect = None
    mock_plyvel.DB.return_value = MagicMock()
