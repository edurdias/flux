"""Tests for HMAC integrity protection of pickled data at rest."""

from __future__ import annotations

import pytest

from flux.config import Configuration
from flux.security.integrity import IntegrityError, sign, verify


@pytest.fixture
def with_key():
    Configuration.get().override(
        security={"encryption": {"encryption_key": "unit-test-integrity-key"}},
    )
    yield
    Configuration.get().reset()


@pytest.fixture
def without_key():
    Configuration.get().override(security={"encryption": {"encryption_key": None}})
    yield
    Configuration.get().reset()


def test_sign_verify_roundtrip(with_key):
    payload = b"\x80\x04 arbitrary pickle bytes"
    signed = sign(payload)
    assert signed != payload
    assert verify(signed) == payload


def test_tampered_payload_rejected(with_key):
    signed = bytearray(sign(b"hello"))
    signed[-1] ^= 0xFF  # flip a byte in the payload
    with pytest.raises(IntegrityError):
        verify(bytes(signed))


def test_unsigned_data_rejected_when_key_present(with_key):
    with pytest.raises(IntegrityError):
        verify(b"unsigned malicious pickle")


def test_no_key_is_passthrough(without_key):
    payload = b"some bytes"
    # Without a key, signing is a no-op and verification is lenient.
    assert sign(payload) == payload
    assert verify(payload) == payload


def test_signed_data_without_key_rejected(with_key):
    signed = sign(b"payload")
    Configuration.get().override(security={"encryption": {"encryption_key": None}})
    with pytest.raises(IntegrityError):
        verify(signed)
