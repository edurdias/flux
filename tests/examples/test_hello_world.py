from __future__ import annotations

from examples.hello_world import hello_world
from flux.domain.events import ExecutionEventType


def test_should_succeed():
    ctx = hello_world.run("Joe")
    assert (
        ctx.has_finished and ctx.has_succeeded
    ), "The workflow should have been completed successfully."
    assert ctx.output == "Hello, Joe"
    return ctx


def test_should_skip_if_finished():
    first_ctx = test_should_succeed()
    second_ctx = hello_world.run(execution_id=first_ctx.execution_id)
    assert first_ctx.execution_id == second_ctx.execution_id
    assert first_ctx.output == second_ctx.output


def test_should_fail():
    ctx = hello_world.run()
    last_event = ctx.events[-1]
    assert last_event.type == ExecutionEventType.WORKFLOW_FAILED
    assert isinstance(last_event.value, TypeError)
