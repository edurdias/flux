"""Tests for server cancellation endpoint."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from flux import ExecutionContext
from flux.domain.events import ExecutionState
from flux.server import Server


@pytest.fixture
def mock_context_manager():
    """Mock context manager for testing."""
    manager = MagicMock()
    manager.get = MagicMock()
    manager.save = MagicMock()
    return manager


@pytest.fixture
def server_app():
    """Create a server app for testing."""
    server = Server(host="localhost", port=8000)
    return server._create_api()


@pytest.fixture
def test_client(server_app):
    """Create a test client for the server app."""
    return TestClient(server_app)


class TestServerCancellation:
    """Tests for server cancellation endpoint."""

    @patch("flux.server.ContextManager.create")
    def test_cancel_workflow_async(self, mock_create, test_client, mock_context_manager):
        """Test cancelling a workflow in async mode."""
        # Set up the mock context manager
        mock_create.return_value = mock_context_manager

        # Create a mock execution context that is running
        ctx = MagicMock(spec=ExecutionContext)
        ctx.has_finished = False
        ctx.start_cancel = MagicMock()
        ctx.state = ExecutionState.RUNNING
        ctx.to_dict = MagicMock(return_value={"state": "RUNNING"})
        ctx.execution_id = "test-execution-id"
        ctx.workflow_id = "test-workflow-id"
        ctx.workflow_name = "test-workflow"

        # Set up the mock to return our context
        mock_context_manager.get.return_value = ctx
        mock_context_manager.save.return_value = ctx

        # Make the request
        response = test_client.get("/workflows/test-workflow/cancel/test-execution-id?mode=async")

        # Check the response
        assert response.status_code == 200

        # Verify the context manager was called correctly
        mock_context_manager.get.assert_called_once_with("test-execution-id")
        ctx.start_cancel.assert_called_once()
        mock_context_manager.save.assert_called_once_with(ctx)

    @patch("flux.server.ContextManager.create")
    def test_cancel_finished_workflow_fails(self, mock_create, test_client, mock_context_manager):
        """Test that cancelling a finished workflow fails."""
        # Set up the mock context manager
        mock_create.return_value = mock_context_manager

        # Create a mock execution context that is finished
        ctx = MagicMock(spec=ExecutionContext)
        ctx.has_finished = True
        ctx.execution_id = "test-execution-id"

        # Set up the mock to return our context
        mock_context_manager.get.return_value = ctx

        # Make the request
        response = test_client.get("/workflows/test-workflow/cancel/test-execution-id?mode=async")

        # Check the response - should be a 400 error
        assert response.status_code == 400
        assert "Cannot cancel a finished execution" in response.text

        # Verify the context manager was called correctly
        mock_context_manager.get.assert_called_once_with("test-execution-id")

        # start_cancel and save should not have been called
        ctx.start_cancel.assert_not_called()
        mock_context_manager.save.assert_not_called()

    @patch("flux.server.ContextManager.create")
    def test_cancel_workflow_sync(self, mock_create, test_client, mock_context_manager):
        """Test cancelling a workflow in sync mode."""
        # This is more complex due to the async/await logic in the endpoint
        # For this test, we'll mock the asyncio.sleep to avoid waiting

        # Set up the mock context manager
        mock_create.return_value = mock_context_manager

        # Create a mock execution context that transitions from running to cancelled
        ctx_running = MagicMock(spec=ExecutionContext)
        ctx_running.has_finished = False
        ctx_running.start_cancel = MagicMock()
        ctx_running.state = ExecutionState.RUNNING
        ctx_running.to_dict = MagicMock(return_value={"state": "RUNNING"})
        ctx_running.execution_id = "test-execution-id"
        ctx_running.workflow_id = "test-workflow-id"
        ctx_running.workflow_name = "test-workflow"

        ctx_cancelled = MagicMock(spec=ExecutionContext)
        ctx_cancelled.has_finished = True
        ctx_cancelled.state = ExecutionState.CANCELLED
        ctx_cancelled.to_dict = MagicMock(return_value={"state": "CANCELLED"})
        ctx_cancelled.execution_id = "test-execution-id"
        ctx_cancelled.workflow_id = "test-workflow-id"
        ctx_cancelled.workflow_name = "test-workflow"

        # Set up the mock to return our contexts in sequence
        mock_context_manager.get.side_effect = [ctx_running, ctx_cancelled]
        mock_context_manager.save.return_value = ctx_running

        # Patch asyncio.sleep to avoid waiting
        with patch("asyncio.sleep", new_callable=AsyncMock):
            # Make the request
            response = test_client.get(
                "/workflows/test-workflow/cancel/test-execution-id?mode=sync",
            )

        # Check the response
        assert response.status_code == 200

        # Verify the context manager was called correctly
        assert mock_context_manager.get.call_count == 2
        ctx_running.start_cancel.assert_called_once()
        mock_context_manager.save.assert_called_once_with(ctx_running)
