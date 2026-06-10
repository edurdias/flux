"""HMAC integrity protection for pickled data at rest.

``dill``/``pickle`` execute arbitrary code on load, so any pickle an attacker
can write to disk (a shared cache directory, an output-storage volume) is a
remote-code-execution vector. This module signs serialized blobs with
HMAC-SHA256 keyed off the configured encryption key and verifies the signature
before the bytes are ever handed to ``dill.loads``.

Behavior:

- When an encryption key is configured, ``sign`` prepends a signature and
  ``verify`` rejects unsigned or tampered data (fail closed).
- When no key is configured, signing is a no-op and verification is lenient,
  preserving the prior behavior for setups that never configured encryption.
  Production deployments should set the encryption key (see SECURITY.md).
"""

from __future__ import annotations

import hashlib
import hmac

from flux.config import Configuration

# Magic prefix identifying a signed blob: 8-byte tag + 32-byte HMAC follow.
_MAGIC = b"FLUXSIG1"
_DIGEST_SIZE = 32  # sha256


class IntegrityError(Exception):
    """Raised when signed data is missing, tampered with, or cannot be verified."""


def _key() -> bytes | None:
    """Derive the HMAC key from the configured encryption key, or None."""
    raw = Configuration.get().settings.security.encryption.encryption_key
    # Guard against non-str values (e.g. MagicMock configs in tests): only a
    # real, non-empty string activates integrity protection.
    if not isinstance(raw, str) or not raw:
        return None
    return hashlib.sha256(f"flux-integrity:{raw}".encode()).digest()


def sign(payload: bytes) -> bytes:
    """Return ``payload`` prefixed with an HMAC signature (no-op without a key)."""
    key = _key()
    if key is None:
        return payload
    tag = hmac.new(key, payload, hashlib.sha256).digest()
    return _MAGIC + tag + payload


def verify(data: bytes) -> bytes:
    """Validate and strip an HMAC signature, returning the original payload.

    Raises:
        IntegrityError: if a key is configured and the data is unsigned or its
            signature does not match.
    """
    key = _key()
    if not data.startswith(_MAGIC):
        # Unsigned data. Reject when a key is configured so a planted, unsigned
        # pickle cannot bypass the integrity check; otherwise pass through.
        if key is not None:
            raise IntegrityError(
                "Refusing to load unsigned data while integrity protection is "
                "enabled (encryption key set). The artifact is missing or was "
                "written before integrity protection was enabled.",
            )
        return data
    if key is None:
        raise IntegrityError("Signed data found but no encryption key is configured.")
    body = data[len(_MAGIC):]
    tag, payload = body[:_DIGEST_SIZE], body[_DIGEST_SIZE:]
    expected = hmac.new(key, payload, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise IntegrityError("Integrity check failed: data has been tampered with.")
    return payload
