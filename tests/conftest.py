"""Top-level test fixtures.

Seeds Configuration values that production now requires explicit configuration
for. The shipped flux.toml no longer hardcodes ``encryption_key`` or
``bootstrap_token``, so any test that exercises a component consuming these
settings (Worker, EncryptedType, secret managers, etc.) needs them seeded.
Living here means the fixture covers tests/flux/, tests/security/,
tests/examples/, and any future test directories.
"""

from __future__ import annotations

import pytest

from flux.config import Configuration
from flux.models import DatabaseRepository


@pytest.fixture(autouse=True)
def _seed_required_config():
    Configuration.get().override(
        workers={"bootstrap_token": "test-bootstrap-token"},
        security={"encryption": {"encryption_key": "test-encryption-key"}},
    )
    yield
    Configuration.get().reset()


@pytest.fixture(autouse=True)
def _reset_db_engine_cache():
    """Clear cached database engines between tests to prevent cross-test contamination.

    Previously lived in tests/flux/conftest.py, which meant tests/security/,
    tests/examples/, and tests/e2e/ inherited stale engines after a config override.
    """
    DatabaseRepository._engines.clear()
    yield
    DatabaseRepository._engines.clear()
