from __future__ import annotations

import pytest

from examples.complex_pipeline import complex_pipeline
from examples.hello_world import hello_world
from flux.context_managers import ContextManager
from flux.errors import ExecutionContextNotFoundError


def test_should_get_existing_context():
    ctx = hello_world.run("Joe")
    assert ctx.finished and ctx.succeeded, "The workflow should have been completed successfully."
    assert ctx.output == "Hello, Joe"

    found = ContextManager.default().get(ctx.execution_id)
    assert found and found.execution_id == ctx.execution_id
    assert found.output == ctx.output


def test_should_raise_exception_when_not_found():
    execution_id = "not_valid"
    with pytest.raises(
        ExecutionContextNotFoundError,
        match=f"Execution context '{execution_id}' not found",
    ):
        ContextManager.default().get(execution_id)


def test_should_save_events_with_exception():
    ctx = complex_pipeline.run({"input_file": "invalid_file.csv"})
    assert ctx.finished and ctx.failed, "The workflow should have failed."
