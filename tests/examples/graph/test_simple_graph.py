from __future__ import annotations

from examples.graph.simple_graph import simple_graph


def test_should_succeed():
    ctx = simple_graph.run("Joe")
    assert ctx.finished and ctx.succeeded, "The workflow should have been completed successfully."
    assert ctx.output == "Hello, Joe"
    return ctx


def test_should_skip_if_finished():
    first_ctx = test_should_succeed()
    second_ctx = simple_graph.run(execution_id=first_ctx.execution_id)
    assert first_ctx.execution_id == second_ctx.execution_id
    assert first_ctx.output == second_ctx.output


def test_should_fail():
    ctx = simple_graph.run()
    assert ctx.finished and ctx.failed, "The workflow should have failed."
