from __future__ import annotations

from examples.nested_tasks import nested_tasks_workflow


def test_should_succeed():
    ctx = nested_tasks_workflow.run()
    assert ctx.finished and ctx.succeeded, "The workflow should have been completed successfully."
    return ctx


def test_should_skip_if_finished():
    first_ctx = test_should_succeed()
    second_ctx = nested_tasks_workflow.run(execution_id=first_ctx.execution_id)
    assert first_ctx.execution_id == second_ctx.execution_id
    assert first_ctx.output == second_ctx.output
