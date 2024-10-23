from __future__ import annotations

from examples.tasks.task_fallback import task_fallback


def test_should_succeed():
    ctx = task_fallback.run()
    assert ctx.finished and ctx.succeeded, "The workflow should have been completed successfully."
    return ctx


def test_should_replay():
    first_ctx = test_should_succeed()
    second_ctx = task_fallback.run(execution_id=first_ctx.execution_id, force_replay=True)
    assert first_ctx.execution_id == second_ctx.execution_id
    assert first_ctx.output == second_ctx.output
