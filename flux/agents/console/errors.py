"""Reading a Flux server error body -- shared by every console surface.

The console endpoints (``console/app.py``) and the terminal console
(``ui/textual_app.py``) both have to answer the same question about a
failed call: what did the server say, and if it was a denial, which
permission is missing? They used to carry a copy each, and the copies
diverged -- one learned the run routes' plural denial shape and the other
did not, so a denied session spawn degraded the web console to read-only
without ever naming the permission. One implementation, imported by both.

(``console.js`` keeps its own copy: the browser bundle has no Python to
import. It is kept deliberately in step with this module.)
"""

from __future__ import annotations

import re
from typing import Any

# Guards against a pathological (or hostile) deeply nested body: the real
# shapes never nest more than twice.
_MAX_DEPTH = 6


def error_detail(exc: Exception) -> Any:
    """The server's error body, if this exception carries one.

    Returns the parsed JSON when the response was JSON (the structured
    denial shapes below), the raw text when it was not, and ``None`` for an
    exception with no response at all (a connection failure, say) so callers
    can tell "the server said nothing" from "the server said something
    unparseable".
    """
    response = getattr(exc, "response", None)
    if response is None:
        return None
    try:
        return response.json()
    except Exception:
        return getattr(response, "text", None) or str(exc)


def missing_permission_of(detail: Any, depth: int = 0) -> str | None:
    """The permission named by a denial body, in every shape the server uses.

    Flux answers a denied request three ways:

    * ``{"error": "forbidden", "missing_permission": "..."}`` -- the
      execution read/approve routes;
    * ``{"message": "...", "missing_permissions": ["...", ...]}`` -- the
      workflow run routes, i.e. what a denied session spawn returns;
    * prose -- ``Permission denied: requires 'x'`` -- from the generic
      permission dependency and the cancel route the write probe uses.

    Any of them can arrive wrapped in FastAPI's ``{"detail": ...}``
    envelope, which nests once more when the console re-raises an upstream
    body verbatim, so this walks the structure rather than indexing it.
    """
    if detail is None or depth > _MAX_DEPTH:
        return None
    if isinstance(detail, str):
        match = re.search(r"requires '([^']+)'", detail)
        return match.group(1) if match else None
    if isinstance(detail, dict):
        permission = detail.get("missing_permission")
        if isinstance(permission, str):
            return permission
        permissions = detail.get("missing_permissions")
        if isinstance(permissions, list) and permissions:
            return str(permissions[0])
        values = list(detail.values())
    elif isinstance(detail, list):
        values = list(detail)
    else:
        return None
    for value in values:
        found = missing_permission_of(value, depth + 1)
        if found is not None:
            return found
    return None
