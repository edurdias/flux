"""Namespace validation — shared between the workflow decorator and the catalog AST parser."""

from __future__ import annotations

import re

DEFAULT_NAMESPACE = "default"
_NAMESPACE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_NAMESPACE_MAX_LEN = 64


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
    return namespace
