from __future__ import annotations

from examples.retries import retries


def test_should_succeed():
    ctx = retries.run()
    assert ctx.finished and ctx.succeeded, "The workflow should have been completed successfully."
    return ctx


def test_should_replay():
    first_ctx = test_should_succeed()
    second_ctx = retries.run(execution_id=first_ctx.execution_id)
    assert first_ctx.execution_id == second_ctx.execution_id
    assert first_ctx.output == second_ctx.output
