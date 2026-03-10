"""Test fixtures for Flux tests."""

import pytest

# Import all fixtures to make them available to tests
from tests.flux.fixtures.config_fixtures import *  # noqa: F403,F401
from tests.flux.fixtures.database_fixtures import *  # noqa: F403,F401

from flux.models import DatabaseRepository


@pytest.fixture(autouse=True)
def _reset_db_engine_cache():
    """Clear cached database engines between tests to prevent cross-test contamination."""
    DatabaseRepository._engines.clear()
    yield
