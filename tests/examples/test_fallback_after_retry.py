from examples.fallback_after_retry import fallback_after_retry


def test_should_succeed():
    ctx = fallback_after_retry.run()
    assert (
        ctx.finished and ctx.succeeded
    ), "The workflow should have be completed and succeed."


def test_should_replay():
    first_ctx = fallback_after_retry.run()
    assert (
        first_ctx.finished and first_ctx.succeeded
    ), "The workflow should have be completed and succeed."

    second_ctx = fallback_after_retry.run(execution_id=first_ctx.execution_id)
    assert first_ctx.events[-1] == second_ctx.events[-1]