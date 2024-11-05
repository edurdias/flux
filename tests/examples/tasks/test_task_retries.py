from __future__ import annotations

from examples.tasks.task_retries import task_retries
from flux.events import ExecutionEventType


def test_should_succeed():
    ctx = task_retries.run()
    assert ctx.finished and ctx.succeeded, "The workflow should have been completed successfully."

    events = [e.type for e in ctx.events]
    assert ExecutionEventType.WORKFLOW_STARTED in events
    assert ExecutionEventType.TASK_STARTED in events
    assert ExecutionEventType.TASK_RETRY_STARTED in events
    assert ExecutionEventType.TASK_RETRY_COMPLETED in events
    assert ExecutionEventType.TASK_COMPLETED in events
    assert ExecutionEventType.WORKFLOW_COMPLETED in events
    return ctx


def test_should_skip_if_finished():
    first_ctx = task_retries.run()
    assert (
        first_ctx.finished and first_ctx.succeeded
    ), "The workflow should have been completed successfully."

    second_ctx = task_retries.run(execution_id=first_ctx.execution_id)
    assert first_ctx.execution_id == second_ctx.execution_id
    assert first_ctx.output == second_ctx.output
