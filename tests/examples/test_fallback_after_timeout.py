from __future__ import annotations

from examples.fallback_after_timeout import fallback_after_timeout


def test_should_succeed():
    ctx = fallback_after_timeout.run()
    assert ctx.finished and ctx.succeeded, "The workflow should have been completed successfully."
    return ctx


def test_should_replay():
    first_ctx = test_should_succeed()
    second_ctx = fallback_after_timeout.run(
        execution_id=first_ctx.execution_id,
    )
    assert first_ctx.execution_id == second_ctx.execution_id
    assert first_ctx.output == second_ctx.output
