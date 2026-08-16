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

# Only for a SecretManager with no synchronous read (see _read_values_sync);
# one thread for the process, not one per redacted payload.
_async_reader: Any = None


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


def _cached(now: float, refresh: bool) -> list[str] | None:
    if refresh or _cache is None or now - _cache[0] >= _CACHE_TTL_SECONDS:
        return None
    return _cache[1]


def _remember(now: float, values: dict[str, Any]) -> list[str]:
    global _cache
    result = _redactable(values)
    _cache = (now, result)
    return result


async def collect_secret_values(*, refresh: bool = False) -> list[str]:
    """All redactable secret values known to the current SecretManager.

    Cached for a short TTL so status polls don't decrypt the whole secret
    store on every request. ``refresh=True`` bypasses the cache (tests).
    """
    now = time.monotonic()
    if (cached := _cached(now, refresh)) is not None:
        return cached

    from flux.secret_managers import SecretManager

    manager = SecretManager.current()
    names = manager.all()
    values = await manager.get(names) if names else {}
    return _remember(now, values)


def collect_secret_values_sync(*, refresh: bool = False) -> list[str]:
    """``collect_secret_values`` for a caller that cannot await, sharing its
    cache.

    Redaction is not only a response concern: a hook's delivery envelope is
    redacted as it is built, on the checkpoint write path, inside the
    caller's open transaction -- and at dispatch that transaction holds row
    locks and the cross-replica dispatch lock. Running the async collector
    from there meant a fresh event loop on a fresh thread per delivery,
    while those locks were held, so the values are read synchronously
    instead. The local store's read is a plain query anyway (its async
    entry point is a thread hop over the same code).
    """
    now = time.monotonic()
    if (cached := _cached(now, refresh)) is not None:
        return cached

    from flux.secret_managers import SecretManager

    manager = SecretManager.current()
    names = manager.all()
    values = _read_values_sync(manager, names) if names else {}
    return _remember(now, values)


def _read_values_sync(manager: Any, names: list[str]) -> dict[str, Any]:
    """Read ``names`` off ``manager`` without a running loop.

    A manager that only offers the coroutine (the remote one a runner child
    is given) still has to be readable here, so its ``get`` is run to
    completion on one long-lived worker thread -- created once for the
    process rather than per call, which is what made this expensive.
    """
    read_sync = getattr(manager, "get_sync", None)
    if read_sync is not None:
        return read_sync(names)

    import asyncio
    import concurrent.futures

    global _async_reader
    if _async_reader is None:
        _async_reader = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="flux-redaction",
        )
    return _async_reader.submit(asyncio.run, manager.get(names)).result()


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


def redact_payload_sync(payload: Any) -> Any:
    """``redact_response`` for a caller that cannot await.

    Same contract, including the best-effort one: a failure is logged and
    the payload returned as it came, because a secrets-store hiccup must
    not fail the checkpoint whose events are being written.
    """
    from flux.config import Configuration

    try:
        if not Configuration.get().settings.security.redact_secrets_in_responses:
            return payload
        values = collect_secret_values_sync()
        if not values:
            return payload
        from fastapi.encoders import jsonable_encoder

        return redact_values(jsonable_encoder(payload), values)
    except Exception:
        logger.error(
            "Secret redaction failed; using the payload unredacted",
            exc_info=True,
        )
        return payload


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
