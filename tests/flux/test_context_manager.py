from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest

from examples.complex_pipeline import complex_pipeline
from examples.hello_world import hello_world
from flux.context_managers import ContextManager
from flux.errors import ExecutionContextNotFoundError


@pytest.fixture
def clean_db():
    """Provide a clean temporary database for tests that need isolation."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = f.name

    db_url = f"sqlite:///{db_path}"
    with patch("flux.config.Configuration.get") as mock_config:
        mock_config.return_value.settings.database_url = db_url
        mock_config.return_value.settings.database_type = "sqlite"
        mock_config.return_value.settings.security.auth.enabled = False

        manager = ContextManager.create()
        yield manager

    if os.path.exists(db_path):
        os.unlink(db_path)


def test_should_get_existing_context():
    ctx = hello_world.run("Joe")
    assert (
        ctx.has_finished and ctx.has_succeeded
    ), "The workflow should have been completed successfully."
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


def test_list_returns_executions(clean_db):
    """Test that list() returns executions."""
    ctx = hello_world.run("ListTest")
    assert ctx.has_finished and ctx.has_succeeded

    manager = ContextManager.create()
    executions, total = manager.list(limit=1000)

    assert total >= 1
    assert len(executions) >= 1

    execution_ids = [e.execution_id for e in executions]
    assert ctx.execution_id in execution_ids


def test_list_filter_by_workflow_name(clean_db):
    """Test that list() filters by workflow name."""
    ctx = hello_world.run("FilterTest")
    assert ctx.has_finished

    manager = ContextManager.create()
    executions, total = manager.list(workflow_name="hello_world")

    assert total >= 1
    for ex in executions:
        assert ex.workflow_name == "hello_world"


def test_list_filter_by_state(clean_db):
    """Test that list() filters by execution state."""
    from flux.domain import ExecutionState

    ctx = hello_world.run("StateTest")
    assert ctx.has_finished and ctx.has_succeeded

    manager = ContextManager.create()
    executions, total = manager.list(state=ExecutionState.COMPLETED)

    for ex in executions:
        assert ex.state == ExecutionState.COMPLETED


def test_list_pagination(clean_db):
    """Test that list() pagination works correctly."""
    manager = ContextManager.create()

    _, initial_total = manager.list(limit=1)

    ctx1 = hello_world.run("PaginationTest1")
    ctx2 = hello_world.run("PaginationTest2")
    ctx3 = hello_world.run("PaginationTest3")

    assert ctx1.has_finished and ctx2.has_finished and ctx3.has_finished

    page1, total1 = manager.list(limit=2, offset=0)
    page2, total2 = manager.list(limit=2, offset=2)

    assert total1 == total2
    assert total1 == initial_total + 3

    assert len(page1) <= 2
    assert len(page2) <= 2

    if len(page1) > 0 and len(page2) > 0:
        page1_ids = {e.execution_id for e in page1}
        page2_ids = {e.execution_id for e in page2}
        assert page1_ids.isdisjoint(page2_ids)


def test_list_returns_correct_total(clean_db):
    """Test that list() returns accurate total count."""
    manager = ContextManager.create()

    _, initial_total = manager.list(limit=10000)

    ctx = hello_world.run("TotalTest")
    assert ctx.has_finished

    _, new_total = manager.list(limit=10000)
    assert new_total >= initial_total + 1


def test_list_combined_filters(clean_db):
    """Test that list() works with multiple filters combined."""
    from flux.domain import ExecutionState

    ctx = hello_world.run("CombinedTest")
    assert ctx.has_finished and ctx.has_succeeded

    manager = ContextManager.create()

    executions, total = manager.list(
        workflow_name="hello_world",
        state=ExecutionState.COMPLETED,
    )

    for ex in executions:
        assert ex.workflow_name == "hello_world"
        assert ex.state == ExecutionState.COMPLETED
