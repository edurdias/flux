from __future__ import annotations

from examples.simple_pipeline import simple_pipeline
from flux.domain.events import ExecutionEventType


def test_should_succeed():
    ctx = simple_pipeline.run(5)
    assert (
        ctx.has_finished and ctx.has_succeeded
    ), "The workflow should have been completed successfully."
    assert ctx.output == 169
    return ctx


def test_should_skip_if_finished():
    first_ctx = test_should_succeed()
    second_ctx = simple_pipeline.run(execution_id=first_ctx.execution_id)
    assert first_ctx.execution_id == second_ctx.execution_id
    assert first_ctx.output == second_ctx.output


def test_should_fail():
    ctx = simple_pipeline.run()
    last_event = ctx.events[-1]
    assert last_event.type == ExecutionEventType.WORKFLOW_FAILED
    assert isinstance(last_event.value, TypeError)
