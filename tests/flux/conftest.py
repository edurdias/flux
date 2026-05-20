"""Test fixtures for Flux tests."""

import os
import tempfile
from unittest.mock import patch

import pytest

# Import all fixtures to make them available to tests
from tests.flux.fixtures.config_fixtures import *  # noqa: F403,F401
from tests.flux.fixtures.database_fixtures import *  # noqa: F403,F401

# _reset_db_engine_cache moved to tests/conftest.py so it covers all test
# directories (tests/security/, tests/examples/, tests/e2e/), not just tests/flux/.


@pytest.fixture
def isolated_db():
    """Yield with config patched to a fresh SQLite DB; cleaned up on exit."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = f.name
    db_url = f"sqlite:///{db_path}"
    with patch("flux.config.Configuration.get") as mock_config:
        mock_config.return_value.settings.database_url = db_url
        mock_config.return_value.settings.database_type = "sqlite"
        mock_config.return_value.settings.security.auth.enabled = False
        yield
    if os.path.exists(db_path):
        os.unlink(db_path)
