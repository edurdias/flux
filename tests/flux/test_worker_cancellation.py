"""Tests for worker cancellation handling."""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from flux import ExecutionContext
from flux.worker import Worker


@pytest.fixture
def mock_client():
    """Mock HTTP client."""
    mock = AsyncMock()
    mock.post = AsyncMock()
    mock.post.return_value.json.return_value = {"session_token": "test-token"}
    mock.post.return_value.raise_for_status = AsyncMock()
    return mock


@pytest.fixture
def worker(mock_client):
    """Create a worker with mocked HTTP client."""
    with patch("flux.worker.httpx.AsyncClient", return_value=mock_client):
        worker = Worker(name="test-worker", server_url="http://localhost:8000")
        worker.session_token = "test-token"
        worker._checkpoint = AsyncMock()
        return worker


@pytest.fixture
def execution_context():
    """Create an execution context for testing."""
    return ExecutionContext(
        workflow_id="test-workflow",
        workflow_name="test",
        execution_id="test-execution-id",
        input="test-input",
    )


class TestWorkerCancellation:
    """Tests for worker cancellation handling."""

    @pytest.mark.asyncio
    async def test_handle_execution_cancelled(self, worker, execution_context):
        """Test that worker handles execution cancelled events correctly."""
        # Create a mock task to be cancelled
        mock_task = MagicMock()
        mock_task.cancel = MagicMock()

        async def mock_await():
            return None

        mock_task.__await__ = mock_await().__await__

        # Add the task to running workflows
        worker._running_workflows = {"test-execution-id": mock_task}

        # Set up the execution context to be cancelled
        execution_context.start_cancel()

        # Create a mock event
        mock_event = MagicMock()
        mock_event.json.return_value = {"context": execution_context.to_dict()}

        # Call the handler
        await worker._handle_execution_cancelled(mock_event)

        # Verify the task was cancelled
        mock_task.cancel.assert_called_once()

        # Verify the task was removed from running workflows
        assert "test-execution-id" not in worker._running_workflows

    @pytest.mark.asyncio
    async def test_handle_execution_cancelled_no_running_task(self, worker, execution_context):
        """Test handling execution cancelled when no task is running."""
        # Set up an empty running workflows dictionary
        worker._running_workflows = {}

        # Set up the execution context to be cancelled
        execution_context.start_cancel()

        # Create a mock event
        mock_event = MagicMock()
        mock_event.json.return_value = {"context": execution_context.to_dict()}

        # Call the handler - should not raise an exception
        await worker._handle_execution_cancelled(mock_event)

        # No assertions needed since we're just ensuring it doesn't throw an exception

    @pytest.mark.asyncio
    async def test_handle_execution_cancelled_task_already_done(self, worker, execution_context):
        """Test handling execution cancelled when task is already done."""
        # Create a mock task that raises CancelledError when awaited
        mock_task = MagicMock()
        mock_task.cancel = MagicMock()

        async def raise_cancelled_error():
            raise asyncio.CancelledError()

        mock_task.__await__ = raise_cancelled_error().__await__

        # Add the task to running workflows
        worker._running_workflows = {"test-execution-id": mock_task}

        # Set up the execution context to be cancelled
        execution_context.start_cancel()

        # Create a mock event
        mock_event = MagicMock()
        mock_event.json.return_value = {"context": execution_context.to_dict()}

        # Call the handler
        await worker._handle_execution_cancelled(mock_event)

        # Verify the task was cancelled
        mock_task.cancel.assert_called_once()

        # Verify the task was removed from running workflows
        assert "test-execution-id" not in worker._running_workflows
