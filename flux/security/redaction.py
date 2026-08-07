"""Presentation-boundary secret redaction (issue #147, phase 1).

Task return values are persisted verbatim to the event log — that store is
the replay substrate, so it cannot be scrubbed at rest without changing
replay behavior. What *can* be scrubbed safely is the presentation
boundary: execution-read API responses (and therefore the CLI, which
renders them) are visible to every holder of ``execution:*:read`` — a far
wider grant than secret-read.

This module redacts, by **value identity**, every string the
``SecretManager`` knows: a task that returned a bare token, or an API
response with an embedded credential, is caught without any author
annotation, whatever the surrounding key is called. Storage-level
redaction (an opt-in ``redact_output`` with explicit re-execute-instead-
of-replay semantics) is phase 2 and deliberately not here.

Redaction is best-effort presentation hygiene: a failure inside it is
logged and the response served unredacted, because breaking every
execution read on a secrets-store hiccup is a worse trade — the event log
itself is unchanged either way.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

REDACTED = "***REDACTED***"

# Values shorter than this are never redacted: scrubbing "1" or "true"
# would riddle responses with false positives while protecting nothing.
MIN_SECRET_LENGTH = 6

# Secret values are re-read (and decrypted) at most this often per process;
# execution reads between refreshes reuse the cached set.
_CACHE_TTL_SECONDS = 30.0

_cache: tuple[float, list[str]] | None = None


def _redactable(values: dict[str, Any]) -> list[str]:
    """The string secret values worth scrubbing, longest first.

    Longest-first ordering keeps a secret that contains another secret as a
    substring from leaving recognizable fragments behind.
    """
    return sorted(
        {v for v in values.values() if isinstance(v, str) and len(v.strip()) >= MIN_SECRET_LENGTH},
        key=len,
        reverse=True,
    )


async def collect_secret_values(*, refresh: bool = False) -> list[str]:
    """All redactable secret values known to the current SecretManager.

    Cached for a short TTL so status polls don't decrypt the whole secret
    store on every request. ``refresh=True`` bypasses the cache (tests).
    """
    global _cache
    now = time.monotonic()
    if not refresh and _cache is not None and now - _cache[0] < _CACHE_TTL_SECONDS:
        return _cache[1]

    from flux.secret_managers import SecretManager

    manager = SecretManager.current()
    names = manager.all()
    values = await manager.get(names) if names else {}
    result = _redactable(values)
    _cache = (now, result)
    return result


def redact_values(obj: Any, values: list[str]) -> Any:
    """Return ``obj`` with every occurrence of ``values`` replaced.

    Walks plain JSON-shaped structures (dict / list / tuple / str); other
    scalars pass through untouched. Keys are scrubbed as well as values — a
    secret used as a dict key is as leaked as one in a value.
    """
    if not values:
        return obj
    if isinstance(obj, str):
        for value in values:
            if value in obj:
                obj = obj.replace(value, REDACTED)
        return obj
    if isinstance(obj, dict):
        return {redact_values(k, values): redact_values(v, values) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_values(item, values) for item in obj]
    return obj


async def redact_response(payload: Any) -> Any:
    """Redact known secret values from an outgoing API payload.

    No-op when ``[flux.security] redact_secrets_in_responses`` is false or
    no redactable secrets exist. The payload is first reduced to plain JSON
    structures with FastAPI's own encoder, so redaction sees exactly the
    representation that would leave the server.
    """
    from flux.config import Configuration

    try:
        if not Configuration.get().settings.security.redact_secrets_in_responses:
            return payload
        values = await collect_secret_values()
        if not values:
            return payload
        from fastapi.encoders import jsonable_encoder

        return redact_values(jsonable_encoder(payload), values)
    except Exception:
        logger.error(
            "Secret redaction failed; serving the response unredacted",
            exc_info=True,
        )
        return payload
