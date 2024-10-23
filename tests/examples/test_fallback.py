from __future__ import annotations

from examples.fallback import fallback


def test_should_succeed():
    ctx = fallback.run()
    assert ctx.finished and ctx.succeeded, "The workflow should have been completed successfully."


def test_should_replay():
    first_ctx = fallback.run()
    assert (
        first_ctx.finished and first_ctx.succeeded
    ), "The workflow should have been completed successfully."

    second_ctx = fallback.run(execution_id=first_ctx.execution_id)
    assert first_ctx.events[-1] == second_ctx.events[-1]
