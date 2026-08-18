"""Deriving a display title from the first user chat message in an execution's log.

Console sessions have no operator-facing name until someone calls
``ConsoleService.rename``; until then every list/detail view falls back to
this. The title comes from the persisted log rather than the live stream, so
what a session is called never depends on which SSE frames a particular
renderer happened to catch (the same source-of-truth rule the rest of the
console follows).
"""

from __future__ import annotations

MAX_TITLE_LEN = 48


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
            # Collapse *internal* whitespace too, not just the ends: a title
            # is drawn on one line (the TUI rail gives it exactly one row),
            # and a pasted multi-line first message would otherwise wrap the
            # row and read as two sessions.
            return truncate_title(" ".join(message.split()))
    return None


def truncate_title(text: str, limit: int = MAX_TITLE_LEN) -> str:
    """Cut ``text`` to ``limit`` on a word boundary.

    Shared with the TUI (flux/agents/ui/textual_app.py): the rail, the
    status line and the server's ``derived_title`` all name the same
    session, and three truncation rules meant three different names for it
    depending on which surface the operator was looking at.
    """
    if len(text) <= limit:
        return text
    # The ellipsis is spent from the budget, not added on top of it: a
    # caller sizing a fixed-width row (the TUI rail) derives its padding
    # from the limit it asked for, and one column more wraps the row --
    # while a surface that hard-cuts at the same number renders a
    # different shape than the one the server sent (#245).
    keep = limit - 1
    if keep <= 0:
        return "…"[:limit]
    cut = text[:keep]
    # Only back up to the previous word when the cut actually split one --
    # i.e. both the last character kept and the one right after it are
    # non-space. When the cut already lands between words, keep it whole.
    if not cut[-1].isspace() and not text[keep].isspace():
        boundary = cut.rfind(" ")
        if boundary > 0:
            cut = cut[:boundary]
    return cut.rstrip() + "…"
