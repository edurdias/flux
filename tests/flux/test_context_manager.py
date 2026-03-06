from __future__ import annotations

import pytest

from examples.complex_pipeline import complex_pipeline
from examples.hello_world import hello_world
from flux.context_managers import ContextManager
from flux.errors import ExecutionContextNotFoundError


def test_should_get_existing_context():
    ctx = hello_world.run("Joe")
    assert ctx.has_finished and ctx.has_succeeded, (
        "The workflow should have been completed successfully."
    )
    assert ctx.output == "Hello, Joe"

    found = ContextManager.create().get(ctx.execution_id)
    assert found and found.execution_id == ctx.execution_id
    assert found.output == ctx.output


def test_should_raise_exception_when_not_found():
    execution_id = "not_valid"
    with pytest.raises(
        ExecutionContextNotFoundError,
        match=f"Execution context '{execution_id}' not found",
    ):
        ContextManager.create().get(execution_id)


def test_should_save_events_with_exception():
    ctx = complex_pipeline.run({"input_file": "invalid_file.csv"})
    assert ctx.has_finished and ctx.has_failed, "The workflow should have failed."


def test_list_returns_executions():
    """Test that list() returns executions."""
    # Run a workflow to create an execution
    ctx = hello_world.run("ListTest")
    assert ctx.has_finished and ctx.has_succeeded

    # List executions with a high limit to ensure we get the one we just created
    manager = ContextManager.create()
    executions, total = manager.list(limit=1000)

    # Should have at least one execution
    assert total >= 1
    assert len(executions) >= 1

    # The execution we just created should be in the list
    execution_ids = [e.execution_id for e in executions]
    assert ctx.execution_id in execution_ids


def test_list_filter_by_workflow_name():
    """Test that list() filters by workflow name."""
    # Run hello_world
    ctx = hello_world.run("FilterTest")
    assert ctx.has_finished

    manager = ContextManager.create()

    # Filter by hello_world workflow
    executions, total = manager.list(workflow_name="hello_world")

    # All returned executions should be hello_world
    assert total >= 1
    for ex in executions:
        assert ex.workflow_name == "hello_world"


def test_list_filter_by_state():
    """Test that list() filters by execution state."""
    from flux.domain import ExecutionState

    # Run a workflow that completes successfully
    ctx = hello_world.run("StateTest")
    assert ctx.has_finished and ctx.has_succeeded

    manager = ContextManager.create()

    # Filter by COMPLETED state
    executions, total = manager.list(state=ExecutionState.COMPLETED)

    # All returned executions should be completed
    for ex in executions:
        assert ex.state == ExecutionState.COMPLETED


def test_list_pagination():
    """Test that list() pagination works correctly."""
    manager = ContextManager.create()

    # Get initial total count
    _, initial_total = manager.list(limit=1)

    # Create multiple executions with unique workflow name to avoid interference
    ctx1 = hello_world.run("PaginationTest1")
    ctx2 = hello_world.run("PaginationTest2")
    ctx3 = hello_world.run("PaginationTest3")

    # Verify all completed
    assert ctx1.has_finished and ctx2.has_finished and ctx3.has_finished

    # Now test pagination - use small limit
    page1, total1 = manager.list(limit=2, offset=0)
    page2, total2 = manager.list(limit=2, offset=2)

    # Total should be the same regardless of pagination
    assert total1 == total2

    # Total should have increased by 3
    assert total1 == initial_total + 3

    # Each page should have at most 2 items (limit is 2)
    assert len(page1) <= 2
    assert len(page2) <= 2

    # If both pages have items, they should be different executions
    if len(page1) > 0 and len(page2) > 0:
        page1_ids = {e.execution_id for e in page1}
        page2_ids = {e.execution_id for e in page2}
        assert page1_ids.isdisjoint(page2_ids)


def test_list_returns_correct_total():
    """Test that list() returns accurate total count."""
    manager = ContextManager.create()

    # Get initial count with high limit to ensure accurate count
    _, initial_total = manager.list(limit=10000)

    # Run a new workflow
    ctx = hello_world.run("TotalTest")
    assert ctx.has_finished

    # Total should increase by at least 1
    _, new_total = manager.list(limit=10000)
    assert new_total >= initial_total + 1


def test_list_combined_filters():
    """Test that list() works with multiple filters combined."""
    from flux.domain import ExecutionState

    # Run a workflow
    ctx = hello_world.run("CombinedTest")
    assert ctx.has_finished and ctx.has_succeeded

    manager = ContextManager.create()

    # Filter by both workflow name and state
    executions, total = manager.list(
        workflow_name="hello_world",
        state=ExecutionState.COMPLETED,
    )

    # All executions should match both filters
    for ex in executions:
        assert ex.workflow_name == "hello_world"
        assert ex.state == ExecutionState.COMPLETED
