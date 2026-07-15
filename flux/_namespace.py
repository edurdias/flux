"""Namespace validation — shared between the workflow decorator and the catalog AST parser."""

from __future__ import annotations

import re

DEFAULT_NAMESPACE = "default"
_NAMESPACE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_NAMESPACE_MAX_LEN = 64

# Namespaces under this prefix belong to agent-authored dynamic workflows:
# entries there are created only through the dynamic registration path, which
# stamps the isolation runner server-side. Ordinary registration (decorators,
# uploads, inline auto-registration) must not be able to squat them with an
# un-stamped workflow, so the shared validator rejects the prefix outright —
# the dynamic path assigns its namespace AFTER parsing, bypassing this check.
RESERVED_DYNAMIC_PREFIX = "dyn-"


def validate_namespace(namespace: str | None) -> str:
    """Normalize and validate a namespace string.

    Returns ``"default"`` for ``None``/``""``. Raises ``ValueError`` for invalid input.
    """
    if namespace is None or namespace == "":
        return DEFAULT_NAMESPACE
    if len(namespace) > _NAMESPACE_MAX_LEN:
        raise ValueError(
            f"Invalid namespace '{namespace}': max length {_NAMESPACE_MAX_LEN}",
        )
    if not _NAMESPACE_RE.match(namespace):
        raise ValueError(
            f"Invalid namespace '{namespace}': must match {_NAMESPACE_RE.pattern}",
        )
    if namespace.startswith(RESERVED_DYNAMIC_PREFIX):
        raise ValueError(
            f"Invalid namespace '{namespace}': the '{RESERVED_DYNAMIC_PREFIX}' prefix is "
            "reserved for agent-authored dynamic workflows",
        )
    return namespace
