from __future__ import annotations

from examples.tasks.task_cache import workflow_with_cached_task


def test_should_succeed():
    ctx = workflow_with_cached_task.run((2, 3, 3))
    assert ctx.finished and ctx.succeeded, "The workflow should have been completed successfully."
    return ctx


def test_should_skip_if_finished():
    first_ctx = test_should_succeed()
    second_ctx = workflow_with_cached_task.run(execution_id=first_ctx.execution_id)
    assert first_ctx.execution_id == second_ctx.execution_id
    assert first_ctx.output == second_ctx.output


def test_should_fail():
    ctx = workflow_with_cached_task.run()
    assert ctx.finished and ctx.failed, "The workflow should have failed."
    assert isinstance(ctx.output, ValueError)
