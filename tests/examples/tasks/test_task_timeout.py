from __future__ import annotations

from examples.tasks.task_timeout import task_nested_timeout
from examples.tasks.task_timeout import task_timeout
from flux.errors import ExecutionTimeoutError


def test_should_timeout():
    ctx = task_timeout.run()
    assert ctx.has_finished and ctx.has_failed, "The workflow should have failed."
    assert isinstance(ctx.output, ExecutionTimeoutError)


def test_should_timeout_even_when_nested():
    ctx = task_nested_timeout.run()
    assert ctx.has_finished and ctx.has_failed, "The workflow should have failed."
    assert isinstance(ctx.output, ExecutionTimeoutError)
