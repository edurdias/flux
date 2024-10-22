from examples.determinism import determinism


def test_should_succeed():
    ctx = determinism.run()
    assert (
        ctx.finished and ctx.succeeded
    ), "The workflow should have been completed successfully."


def test_should_replay():
    first_ctx = determinism.run()
    assert (
        first_ctx.finished and first_ctx.succeeded
    ), "The workflow should have been completed successfully."

    second_ctx = determinism.run(execution_id=first_ctx.execution_id)
    assert first_ctx.execution_id == second_ctx.execution_id
    assert first_ctx.output == second_ctx.output
