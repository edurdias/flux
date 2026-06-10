"""Top-level test fixtures.

Seeds Configuration values that production now requires explicit configuration
for. The shipped flux.toml no longer hardcodes ``encryption_key`` or
``bootstrap_token``, so any test that exercises a component consuming these
settings (Worker, EncryptedType, secret managers, etc.) needs them seeded.
Living here means the fixture covers tests/flux/, tests/security/,
tests/examples/, and any future test directories.
"""

from __future__ import annotations

import os

import pytest

# Auth is disabled in the unit suite, and the secure-default middleware blocks
# anonymous state-changing requests unless this is set. Setting it in the
# environment (not just via Configuration.override) ensures it survives the
# tests that reset the config singleton and rebuild it from env.
os.environ.setdefault("FLUX_SECURITY__AUTH__ALLOW_ANONYMOUS", "true")

from flux.config import Configuration  # noqa: E402
from flux.models import DatabaseRepository  # noqa: E402


@pytest.fixture(autouse=True)
def _seed_required_config():
    Configuration.get().override(
        workers={"bootstrap_token": "test-bootstrap-token"},
        security={
            "encryption": {"encryption_key": "test-encryption-key"},
            # Auth is disabled in most tests; permit anonymous mutations so the
            # secure-default middleware doesn't block them. Tests that exercise
            # the deny path override this explicitly.
            "auth": {"allow_anonymous": True},
        },
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
