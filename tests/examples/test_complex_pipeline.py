from __future__ import annotations

from examples import complex_pipeline


def test_should_succeed():
    input = {
        "input_file": "examples/data/sample.csv",
        "output_file": ".data/sample_output.csv",
    }

    ctx = complex_pipeline.run(input)
    assert ctx.finished and ctx.succeeded, "The workflow should have been completed successfully."

    return ctx


def test_should_skip_if_finished():
    first_ctx = test_should_succeed()
    second_ctx = complex_pipeline.run(execution_id=first_ctx.execution_id)

    assert (
        second_ctx.finished and second_ctx.succeeded
    ), "The workflow should have been completed successfully."

    assert first_ctx.execution_id == second_ctx.execution_id
    assert first_ctx.output == second_ctx.output


def test_should_fail_no_input():
    ctx = complex_pipeline.run()
    assert ctx.finished and ctx.failed, "The workflow should have failed."


def test_should_fail_invalid_input_file():
    input = {"input_file": "examples/data/invalid.csv"}
    ctx = complex_pipeline.run(input)
    assert ctx.finished and ctx.failed, "The workflow should have failed."

    assert isinstance(ctx.output, FileNotFoundError)
