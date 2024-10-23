from __future__ import annotations

from examples.sleep import sleep_workflow


def test_should_succeed():
    ctx = sleep_workflow.run()
    assert ctx.finished and ctx.succeeded, "The workflow should have been completed successfully."
    return ctx


def test_should_replay():
    first_ctx = test_should_succeed()
    second_ctx = sleep_workflow.run(execution_id=first_ctx.execution_id)
    assert first_ctx.execution_id == second_ctx.execution_id
    assert first_ctx.output == second_ctx.output
