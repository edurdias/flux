"""Deriving a display title from the first user chat message in an execution's log.

Console sessions have no operator-facing name until someone calls
``ConsoleService.rename``; until then every list/detail view falls back to
this. The title comes from the persisted log rather than the live stream, so
what a session is called never depends on which SSE frames a particular
renderer happened to catch (the same source-of-truth rule the rest of the
console follows).
"""

from __future__ import annotations

_MAX_TITLE_LEN = 48


def derived_title(detail: dict) -> str | None:
    """Return a title derived from the first user message, or None if there isn't one yet.

    ``resume()`` records a resumed workflow's raw input verbatim as the
    ``WORKFLOW_RESUMED`` event value (flux/domain/execution_context.py); for
    every chat workflow the console drives (``agent_chat`` and
    ``agent_custom_*``), that input is ``{"message": <text>}``, exactly what
    ``ConsoleService.send`` posts. Other resume shapes -- elicitation
    responses, bare pause wakeups -- carry no ``message`` key and are
    skipped, so the scan lands on the first genuine chat turn regardless of
    what else resumed the execution first.
    """
    for event in detail.get("events") or ():
        if not isinstance(event, dict) or event.get("type") != "WORKFLOW_RESUMED":
            continue
        value = event.get("value")
        if not isinstance(value, dict):
            continue
        message = value.get("message")
        if isinstance(message, str) and message.strip():
            return _truncate(message.strip())
    return None


def _truncate(text: str, limit: int = _MAX_TITLE_LEN) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # Only back up to the previous word when the cut actually split one --
    # i.e. both the last character kept and the one right after it are
    # non-space. When the cut already lands between words, keep it whole.
    if not cut[-1].isspace() and not text[limit].isspace():
        boundary = cut.rfind(" ")
        if boundary > 0:
            cut = cut[:boundary]
    return cut.rstrip() + "…"
