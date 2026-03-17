from __future__ import annotations

from typing import Any

from flux import ExecutionContext
from flux.domain.events import ExecutionEvent
from flux.domain.events import ExecutionEventType
from flux.errors import PauseRequested
import flux

from flux.task import TaskMetadata


@flux.task.with_options(metadata=True)
async def pause(name: str, output: Any = None, *, metadata: TaskMetadata):
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
    raise PauseRequested(name=name, output=output)
