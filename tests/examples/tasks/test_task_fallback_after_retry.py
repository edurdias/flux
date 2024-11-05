from __future__ import annotations

from examples.tasks.task_fallback_after_retry import task_fallback_after_retry
from flux.events import ExecutionEventType


def test_should_succeed():
    ctx = task_fallback_after_retry.run()
    assert ctx.finished and ctx.succeeded, "The workflow should have been completed successfully."

    events = [e.type for e in ctx.events]
    assert ExecutionEventType.WORKFLOW_STARTED in events
    assert ExecutionEventType.TASK_STARTED in events
    assert ExecutionEventType.TASK_RETRY_STARTED in events
    assert ExecutionEventType.TASK_RETRY_FAILED in events
    assert ExecutionEventType.TASK_FALLBACK_STARTED in events
    assert ExecutionEventType.TASK_FALLBACK_COMPLETED in events
    assert ExecutionEventType.TASK_COMPLETED in events
    assert ExecutionEventType.WORKFLOW_COMPLETED in events

    return ctx


def test_should_skip_if_finished():
    first_ctx = test_should_succeed()
    second_ctx = task_fallback_after_retry.run(execution_id=first_ctx.execution_id)
    assert first_ctx.execution_id == second_ctx.execution_id
    assert first_ctx.output == second_ctx.output
