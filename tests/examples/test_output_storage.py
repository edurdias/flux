from __future__ import annotations

from examples.output_storage import output_storage
from flux.output_storage import OutputStorageReference


def test_should_succeed():
    ctx = output_storage.run("examples/data/sample.csv")
    assert ctx.finished and ctx.succeeded, "The workflow should have been completed successfully."
    assert isinstance(ctx.output, OutputStorageReference)
    assert ctx.output.reference_id == f"{output_storage.name}_{ctx.execution_id}"
    return ctx


def test_should_skip_if_finished():
    first_ctx = test_should_succeed()
    second_ctx = output_storage.run(execution_id=first_ctx.execution_id)
    assert first_ctx.execution_id == second_ctx.execution_id
    assert first_ctx.output == second_ctx.output


def test_should_fail():
    ctx = output_storage.run()
    assert ctx.finished and ctx.failed, "The workflow should have failed."
