from __future__ import annotations

from examples.subflows import subflows
from flux.events import ExecutionEventType


def test_should_succeed():
    repos = ["python/cpython", "microsoft/vscode", "localsend/localsend"]
    ctx = subflows.run(repos)
    assert ctx.finished and ctx.succeeded, "The workflow should have been completed successfully."
    assert all(
        repo in ctx.output for repo in repos
    ), "The output should contain all the specified repositories."
    return ctx


def test_should_replay():
    first_ctx = test_should_succeed()
    second_ctx = subflows.run(execution_id=first_ctx.execution_id, force_replay=True)
    assert first_ctx.execution_id == second_ctx.execution_id
    assert first_ctx.output == second_ctx.output


def test_should_fail_no_input():
    ctx = subflows.run()
    last_event = ctx.events[-1]
    assert last_event.type == ExecutionEventType.WORKFLOW_FAILED
    assert isinstance(last_event.value, TypeError)


def test_should_fail_empty_list():
    ctx = subflows.run([])
    last_event = ctx.events[-1]
    assert last_event.type == ExecutionEventType.WORKFLOW_FAILED
    assert isinstance(last_event.value, TypeError)
