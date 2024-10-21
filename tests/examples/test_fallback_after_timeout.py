from examples.fallback_after_timeout import fallback_after_timeout


def test_should_succeed():
    ctx = fallback_after_timeout.run()
    assert (
        ctx.finished and ctx.succeeded
    ), "The workflow should have be completed and succeed."


def test_should_replay():
    first_ctx = fallback_after_timeout.run()
    assert (
        first_ctx.finished and first_ctx.succeeded
    ), "The workflow should have be completed and succeed."

    second_ctx = fallback_after_timeout.run(execution_id=first_ctx.execution_id)
    assert first_ctx.events[-1] == second_ctx.events[-1]
