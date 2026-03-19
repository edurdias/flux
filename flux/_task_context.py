from __future__ import annotations

from contextvars import ContextVar

_CURRENT_TASK: ContextVar[tuple[str, str] | None] = ContextVar("current_task", default=None)
