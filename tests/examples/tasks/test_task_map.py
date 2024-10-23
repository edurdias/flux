from __future__ import annotations

from examples.tasks.task_map import task_map
from flux.events import ExecutionEventType


def test_should_succeed():
    ctx = task_map.run(2)
    assert ctx.finished and ctx.succeeded, "The workflow should have been completed successfully."
    return ctx


def test_should_replay():
    first_ctx = test_should_succeed()
    second_ctx = task_map.run(execution_id=first_ctx.execution_id, force_replay=True)
    assert first_ctx.execution_id == second_ctx.execution_id
    assert first_ctx.output == second_ctx.output


def test_should_fail():
    ctx = task_map.run()
    last_event = ctx.events[-1]
    assert last_event.type == ExecutionEventType.WORKFLOW_FAILED
    assert isinstance(last_event.value, TypeError)
