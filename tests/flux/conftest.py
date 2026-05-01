"""Test fixtures for Flux tests."""

import pytest

# Import all fixtures to make them available to tests
from tests.flux.fixtures.config_fixtures import *  # noqa: F403,F401
from tests.flux.fixtures.database_fixtures import *  # noqa: F403,F401

from flux.config import Configuration
from flux.models import DatabaseRepository


@pytest.fixture(autouse=True)
def _reset_db_engine_cache():
    """Clear cached database engines between tests to prevent cross-test contamination."""
    DatabaseRepository._engines.clear()
    yield


@pytest.fixture(autouse=True)
def _seed_required_config():
    """Provide values for fields that production now requires explicit configuration for.

    The shipped flux.toml no longer hardcodes ``encryption_key`` or
    ``bootstrap_token``, so tests that exercise components consuming these
    settings (Worker, EncryptedType, etc.) need them seeded. We override the
    Configuration singleton before each test and reset after.
    """
    Configuration.get().override(
        workers={"bootstrap_token": "test-bootstrap-token"},
        security={"encryption": {"encryption_key": "test-encryption-key"}},
    )
    yield
    Configuration.get().reset()
