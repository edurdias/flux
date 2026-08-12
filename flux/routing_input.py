"""Validation for routing-only execution input (issue #211).

Every ingress path — the run header, the schedule field, ``call()``, the CLI —
goes through :func:`validate_routing_input`, so the rules cannot drift between
them.

Rejected, never dropped. A silently discarded routing directive does not fail:
it routes the execution somewhere the caller did not intend, and from outside
that is indistinguishable from having been honoured.
"""

from __future__ import annotations

import json
from typing import Any

# A header is not a payload channel, and servers cap header bytes anyway. An
# explicit limit makes the rejection ours and legible rather than a proxy's
# opaque 431.
MAX_ROUTING_INPUT_BYTES = 4096
MAX_ROUTING_INPUT_KEYS = 64
MAX_ROUTING_INPUT_DEPTH = 8

_SCALARS = (str, int, float, bool)


class RoutingInputError(ValueError):
    """Raised for any routing input a caller cannot have meant."""


def parse_routing_input_header(raw: str | list[str] | None) -> dict[str, Any] | None:
    """Parse and validate the ``X-Flux-Routing-Input`` header value.

    The route declares this header as ``list[str]`` so FastAPI reads it with
    ``getlist()``. Declared as ``str`` it would use ``get()``, which returns
    only the first of repeated headers — silently discarding the rest, which
    is the failure this module exists to refuse.
    """
    if raw is None:
        return None
    if isinstance(raw, list):
        if not raw:
            return None
        if len(raw) > 1:
            raise RoutingInputError(
                f"X-Flux-Routing-Input must be sent once; received it {len(raw)} times",
            )
        raw = raw[0]
    if len(raw.encode("utf-8")) > MAX_ROUTING_INPUT_BYTES:
        raise RoutingInputError(
            f"X-Flux-Routing-Input exceeds {MAX_ROUTING_INPUT_BYTES} bytes",
        )
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RoutingInputError(f"X-Flux-Routing-Input is not valid JSON: {e}") from e
    return validate_routing_input(parsed)


def validate_routing_input(value: Any) -> dict[str, Any] | None:
    """Validate an already-parsed routing input mapping."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise RoutingInputError(
            f"routing input must be a JSON object, got {type(value).__name__}",
        )
    if not value:
        return None
    encoded = json.dumps(value, default=str)
    if len(encoded.encode("utf-8")) > MAX_ROUTING_INPUT_BYTES:
        raise RoutingInputError(f"routing input exceeds {MAX_ROUTING_INPUT_BYTES} bytes")
    _check_mapping(value, depth=1, seen_keys=[0])
    return dict(value)


def _check_mapping(mapping: dict, depth: int, seen_keys: list[int]) -> None:
    if depth > MAX_ROUTING_INPUT_DEPTH:
        raise RoutingInputError(
            f"routing input nests deeper than {MAX_ROUTING_INPUT_DEPTH} levels",
        )
    for key, item in mapping.items():
        if not isinstance(key, str) or not key:
            raise RoutingInputError(f"routing input keys must be non-empty strings, got {key!r}")
        if "." in key:
            # Path resolution splits the whole path on dots and descends one
            # level per part, so a dotted key is unreachable at ANY depth --
            # {"a": {"b.c": 1}} is as unreachable as {"a.b": 1}.
            raise RoutingInputError(
                f"routing input key '{key}' contains '.', which path resolution "
                "reserves for descending nested objects",
            )
        seen_keys[0] += 1
        if seen_keys[0] > MAX_ROUTING_INPUT_KEYS:
            raise RoutingInputError(f"routing input has more than {MAX_ROUTING_INPUT_KEYS} keys")
        if isinstance(item, dict):
            _check_mapping(item, depth + 1, seen_keys)
        elif item is not None and not isinstance(item, _SCALARS):
            raise RoutingInputError(
                f"routing input value for '{key}' must be a scalar or object, "
                f"got {type(item).__name__}",
            )


def parse_cli_pairs(pairs: tuple[str, ...] | list[str] | None) -> dict[str, str] | None:
    """Build routing input from repeatable ``key=value`` CLI arguments.

    Flat by construction: `key=value` cannot express nesting, and dots are
    refused in keys, so nested structures are an API/SDK affair.
    """
    if not pairs:
        return None
    out: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise RoutingInputError(f"routing input must be key=value, got '{pair}'")
        if key in out:
            raise RoutingInputError(f"routing input key '{key}' given more than once")
        out[key] = value
    return validate_routing_input(out)
