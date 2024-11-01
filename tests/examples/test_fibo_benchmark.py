from __future__ import annotations

from examples.fibo_benchmark import fibo_benchmark


def test_should_succeed():
    expected_output = {"Iteration #0": 13, "Iteration #1": 13}
    ctx = fibo_benchmark.run((2, 7))
    assert ctx.finished and ctx.succeeded, "The workflow should have been completed successfully."
    assert ctx.output == expected_output
    return ctx


def test_should_skip_if_finished():
    first_ctx = test_should_succeed()
    second_ctx = fibo_benchmark.run(execution_id=first_ctx.execution_id)
    assert first_ctx.execution_id == second_ctx.execution_id
    assert first_ctx.output == second_ctx.output
