from __future__ import annotations

from typing import Any

from flux import ExecutionContext
from flux._task_context import _CURRENT_TASK


async def progress(value: Any) -> None:
    """Report ephemeral progress from within a task.

    Progress events are streamed to connected clients in real-time but are
    never persisted to the event log, stored in the database, or replayed.

    Args:
        value: Any value to report as progress.
    """
    task_info = _CURRENT_TASK.get()
    if task_info is None:
        return
    task_id, task_name = task_info
    ctx = await ExecutionContext.get()
    await ctx.emit_progress(task_id, task_name, value)
