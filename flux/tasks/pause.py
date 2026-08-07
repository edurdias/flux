from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from flux import ExecutionContext
from flux.domain.events import ExecutionEvent
from flux.domain.events import ExecutionEventType
from flux.errors import ExecutionError, PauseRequested
from flux.task import TaskMetadata
from flux.task import task


@task.with_options(metadata=True)
async def pause(
    name: str,
    output: Any = None,
    *,
    until: datetime | None = None,
    after: timedelta | None = None,
    on_complete: str | None = None,
    metadata: TaskMetadata,
):
    """Pause the execution, optionally with a wake condition (issue #145).

    Without a wake condition the pause is indefinite — something external
    must resume the execution, as always. With one, the execution still goes
    ``PAUSED`` and releases its worker slot, but the server's scheduler tick
    resumes it when the condition fires:

    - ``until=datetime``: wake at an absolute instant (naive datetimes are
      taken as UTC).
    - ``after=timedelta``: wake after a relative delay, resolved to an
      absolute instant *now* so replaying the pause re-records the same wake.
    - ``on_complete=execution_id``: wake when the named execution reaches a
      terminal state (completed, failed, or cancelled).

    Exactly one condition may be given. Wakes fire on the server's scheduler
    tick, so they apply to the distributed path; an inline
    (``workflow.run()``) execution has no scheduler and must be resumed
    manually regardless.
    """
    ctx = await ExecutionContext.get()

    if ctx.is_resuming:
        input = ctx.resume()
        ctx.events.append(
            ExecutionEvent(
                type=ExecutionEventType.TASK_RESUMED,
                source_id=metadata.task_id,
                name=metadata.task_name,
                value={"name": name, "input": input},
            ),
        )
        return input

    given = [c for c in (until, after, on_complete) if c is not None]
    if len(given) > 1:
        raise ExecutionError(
            message="pause() accepts at most one of until=, after=, on_complete=",
        )

    wake_at: str | None = None
    if until is not None:
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        # Normalize aware datetimes to UTC so the stored instant carries one
        # consistent offset, matching the docstring and the scheduler's
        # UTC-based comparisons.
        wake_at = until.astimezone(timezone.utc).isoformat()
    elif after is not None:
        if after.total_seconds() <= 0:
            raise ExecutionError(message="pause(after=...) must be a positive timedelta")
        wake_at = (datetime.now(timezone.utc) + after).isoformat()

    raise PauseRequested(
        name=name,
        output=output,
        wake_at=wake_at,
        wake_on_complete=on_complete,
    )
