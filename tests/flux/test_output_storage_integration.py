from __future__ import annotations

from unittest.mock import patch

import pytest

from flux.context import WorkflowExecutionContext
from flux.decorators import task
from flux.decorators import workflow
from flux.output_storage import InlineOutputStorage
from flux.output_storage import OutputStorageReference


@pytest.fixture
def inline_storage():
    """Create an inline storage instance for testing."""
    return InlineOutputStorage()


@pytest.fixture
def task_with_storage(inline_storage):
    """Create a task that uses inline storage."""

    @task.with_options(output_storage=inline_storage)
    async def task_with_inline_storage(value):
        return value

    return task_with_inline_storage


@pytest.fixture
def workflow_with_storage(inline_storage, task_with_storage):
    """Create a workflow that uses inline storage."""

    @workflow.with_options(output_storage=inline_storage)
    async def workflow_with_inline_storage(ctx: WorkflowExecutionContext):
        result = await task_with_storage(ctx.input)
        return result

    return workflow_with_inline_storage


def test_task_with_inline_storage(inline_storage, task_with_storage):
    """Test that a task uses inline storage correctly when executed in a workflow."""

    # Create a workflow that uses the task
    @workflow.with_options(output_storage=inline_storage)
    async def wrapper_workflow(ctx: WorkflowExecutionContext):
        result = await task_with_storage("test_value")
        return result

    # Run the workflow
    ctx = wrapper_workflow.run()

    # Verify the workflow completed successfully
    assert ctx.finished
    assert ctx.succeeded

    # Verify the output is stored correctly
    assert isinstance(ctx.output, OutputStorageReference)

    # Retrieve the output value
    output_value = inline_storage.retrieve(ctx.output)
    assert output_value == "test_value"


def test_workflow_with_inline_storage(inline_storage, workflow_with_storage):
    """Test that a workflow uses inline storage correctly."""
    # Run the workflow
    ctx = workflow_with_storage.run("workflow_value")

    # Verify the workflow completed successfully
    assert ctx.finished
    assert ctx.succeeded

    # Verify the output is stored correctly
    assert isinstance(ctx.output, OutputStorageReference)
    assert ctx.output.storage_type == "inline"

    # Retrieve the output value
    output_value = inline_storage.retrieve(ctx.output)
    assert output_value == "workflow_value"


def test_workflow_with_configured_storage():
    """Test using a configured storage with a workflow."""
    with patch("flux.output_storage.LocalFileStorage") as mock_storage_class:
        # Mock the storage instance
        mock_storage = mock_storage_class.return_value
        mock_storage.store.return_value = OutputStorageReference(
            storage_type="mock_storage",
            reference_id="test_ref_id",
            metadata={"mock": True},
        )

        # Define a workflow with the mock storage
        @workflow.with_options(output_storage=mock_storage)
        async def workflow_with_mock_storage(ctx: WorkflowExecutionContext):
            return ctx.input

        # Run the workflow
        ctx = workflow_with_mock_storage.run("mock_value")

        # Verify the storage was used
        mock_storage.store.assert_called_once()
        assert ctx.output.storage_type == "mock_storage"
        assert ctx.output.reference_id == "test_ref_id"


def test_task_with_missing_reference():
    """Test handling of missing reference errors."""
    inline_storage = InlineOutputStorage()

    # Create a reference to non-existent output
    bad_reference = OutputStorageReference(
        storage_type="inline",
        reference_id="non-existent",
        metadata={},  # Missing the 'value' key required by InlineOutputStorage
    )

    # Attempting to retrieve should raise KeyError
    with pytest.raises(KeyError):
        inline_storage.retrieve(bad_reference)
