from __future__ import annotations

from examples.pause_with_input import pause_with_input_workflow


def test_should_succeed():
    ctx = pause_with_input_workflow.run("Joe")
    assert ctx.paused, "The workflow should have been paused."
    ctx = pause_with_input_workflow.run(input="Joe", execution_id=ctx.execution_id)
    assert ctx.finished and ctx.succeeded, "The workflow should have been completed successfully."
    assert ctx.output == "Hello, Joe"
    return ctx


def test_should_skip_if_finished():
    first_ctx = test_should_succeed()
    second_ctx = pause_with_input_workflow.run(input="Joe", execution_id=first_ctx.execution_id)
    assert first_ctx.execution_id == second_ctx.execution_id
    assert first_ctx.output == second_ctx.output
