from __future__ import annotations

from examples.subflows_map import subflows_map_workflow
from flux.events import ExecutionEventType


def test_should_succeed():
    repos = ["python/cpython", "microsoft/vscode"]
    ctx = subflows_map_workflow.run(repos)
    assert ctx.finished and ctx.succeeded, "The workflow should have been completed successfully."
    assert all(
        repo in ctx.output for repo in repos
    ), "The output should contain all the specified repositories."
    return ctx


def test_should_skip_if_finished():
    first_ctx = test_should_succeed()
    second_ctx = subflows_map_workflow.run(execution_id=first_ctx.execution_id)
    assert first_ctx.execution_id == second_ctx.execution_id
    assert first_ctx.output == second_ctx.output


def test_should_fail_no_input():
    ctx = subflows_map_workflow.run()
    last_event = ctx.events[-1]
    assert last_event.type == ExecutionEventType.WORKFLOW_FAILED
    assert isinstance(last_event.value, TypeError)


def test_should_fail_empty_list():
    ctx = subflows_map_workflow.run([])
    last_event = ctx.events[-1]
    assert last_event.type == ExecutionEventType.WORKFLOW_FAILED
    assert isinstance(last_event.value, TypeError)
