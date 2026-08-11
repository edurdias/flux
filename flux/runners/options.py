"""Per-workflow runner options.

Options travel with the workflow, so their values are author-controlled. They
may only ever *narrow* what the worker's configuration already allows: the
runner takes the stricter of the two, which makes widening impossible rather
than merely forbidden.
"""

from __future__ import annotations

import re
from typing import Any

# Closed set. A key absent here is rejected at registration so a typo fails
# loudly rather than being silently dropped at dispatch.
KNOWN_RUNNER_OPTIONS = ("cpus", "memory", "timeout", "network", "read_only")

_MEMORY_RE = re.compile(r"^(\d+)\s*([kmgKMG]?)b?$", re.IGNORECASE)
_MEMORY_UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}


def parse_memory(value: str | int) -> int:
    """Bytes from a docker-style size (``512m``, ``1g``, or a bare number)."""
    if isinstance(value, bool):
        raise ValueError(f"invalid memory value: {value!r}")
    if isinstance(value, int):
        return value
    match = _MEMORY_RE.match(str(value).strip())
    if not match:
        raise ValueError(f"invalid memory value: {value!r}")
    return int(match.group(1)) * _MEMORY_UNITS[match.group(2).lower()]


def validate_runner_options(options: dict[str, Any] | None) -> dict[str, Any]:
    """Reject anything that is not a known knob set to a tightening value."""
    if not options:
        return {}
    if not isinstance(options, dict):
        raise ValueError("runner_options must be a mapping")

    for key, value in options.items():
        if key not in KNOWN_RUNNER_OPTIONS:
            raise ValueError(
                f"unknown runner option '{key}'; known options: {', '.join(KNOWN_RUNNER_OPTIONS)}",
            )
        if key in ("cpus", "timeout"):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"runner option '{key}' must be a positive number, got {value!r}")
        elif key == "memory":
            # A zero or negative size passes the grammar but is not a limit
            # docker can apply; reject it here so it fails at registration.
            if parse_memory(value) <= 0:
                raise ValueError(
                    f"runner option 'memory' must be a positive size, got {value!r}",
                )
        elif key == "network":
            # Only dropping the network narrows; naming one could widen.
            if value != "none":
                raise ValueError(
                    f"runner option 'network' may only be 'none', got {value!r}",
                )
        elif key == "read_only" and value is not True:
            raise ValueError(f"runner option 'read_only' may only be True, got {value!r}")
    return dict(options)


def tighten_options(configured: dict[str, Any], requested: dict[str, Any]) -> dict[str, Any]:
    """Merge a request into the worker's configuration, keeping the stricter.

    Widening is not rejected here, it simply does not happen — the caller
    always gets a profile at least as narrow as the one it configured.
    """
    merged = dict(configured)
    for key, value in (requested or {}).items():
        current = configured.get(key)
        # A falsy configured value means "unset" for every knob here (0 cpus,
        # 0 timeout and "" memory all mean unlimited), so the request applies
        # whole rather than being min()'d against a bound that isn't one.
        if key in ("cpus", "timeout"):
            merged[key] = value if not current else min(current, value)
        elif key == "memory":
            merged[key] = value if not current else min(current, value, key=parse_memory)
        elif key == "network":
            # Config having already dropped it wins; otherwise 'none' narrows.
            merged[key] = "none" if "none" in (current, value) else current
        elif key == "read_only":
            merged[key] = bool(current) or bool(value)
    return merged
