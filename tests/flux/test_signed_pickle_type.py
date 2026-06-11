"""SignedPickleType: HMAC integrity for at-rest dill columns.

Execution input/output, event values, and schedule input are signed on write
and strictly verified on read so a tampered or pre-integrity DB row cannot be
deserialized (dill executes code on load).
"""

from __future__ import annotations

import dill
import pytest

from flux.config import Configuration
from flux.models import SignedPickleType
from flux.security.integrity import IntegrityError


@pytest.fixture
def with_key():
    Configuration.get().override(
        security={"encryption": {"encryption_key": "signed-pickle-test-key"}},
    )
    yield
    Configuration.get().reset()


@pytest.fixture
def without_key():
    Configuration.get().override(security={"encryption": {"encryption_key": None}})
    yield
    Configuration.get().reset()


def test_roundtrip_signs_and_verifies(with_key):
    t = SignedPickleType()
    stored = t.process_bind_param({"x": [1, 2, 3]}, None)
    assert stored is not None and stored.startswith(b"FLUXSIG1")
    assert t.process_result_value(stored, None) == {"x": [1, 2, 3]}


def test_none_passthrough(with_key):
    t = SignedPickleType()
    assert t.process_bind_param(None, None) is None
    assert t.process_result_value(None, None) is None


def test_tampered_row_rejected(with_key):
    t = SignedPickleType()
    stored = bytearray(t.process_bind_param({"x": 1}, None))
    stored[-1] ^= 0xFF
    with pytest.raises(IntegrityError):
        t.process_result_value(bytes(stored), None)


def test_unsigned_legacy_row_rejected_when_key_set(with_key):
    t = SignedPickleType()
    # A row written before integrity protection (plain dill, no signature).
    legacy = dill.dumps({"x": 1})
    with pytest.raises(IntegrityError):
        t.process_result_value(legacy, None)


def test_no_key_behaves_like_plain_dill(without_key):
    # Without an encryption key, signing is a no-op and verification is lenient
    # so deployments that never configured encryption keep working.
    t = SignedPickleType()
    stored = t.process_bind_param({"x": 1}, None)
    assert not stored.startswith(b"FLUXSIG1")  # no signature prefix
    assert t.process_result_value(stored, None) == {"x": 1}
    # A pre-existing plain-dill row remains readable when no key is set.
    assert t.process_result_value(dill.dumps({"y": 2}), None) == {"y": 2}
