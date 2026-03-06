"""Tests for server cancellation endpoint."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from flux import ExecutionContext
from flux.domain.events import ExecutionState
from flux.errors import WorkerNotFoundError
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


class TestWorkflowEndpoints:
    """Tests for workflow management endpoints."""

    @patch("flux.server.WorkflowCatalog.create")
    def test_workflow_delete(self, mock_catalog_create, test_client):
        """Test deleting a workflow."""
        mock_catalog = MagicMock()
        mock_catalog.delete.return_value = None
        mock_catalog_create.return_value = mock_catalog

        response = test_client.delete("/workflows/test_workflow")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        mock_catalog.delete.assert_called_once_with("test_workflow", None)

    @patch("flux.server.WorkflowCatalog.create")
    def test_workflow_delete_specific_version(self, mock_catalog_create, test_client):
        """Test deleting a specific workflow version."""
        mock_catalog = MagicMock()
        mock_catalog.delete.return_value = None
        mock_catalog_create.return_value = mock_catalog

        response = test_client.delete("/workflows/test_workflow?version=2")

        assert response.status_code == 200
        mock_catalog.delete.assert_called_once_with("test_workflow", 2)

    @patch("flux.server.WorkflowCatalog.create")
    def test_workflow_versions_list(self, mock_catalog_create, test_client):
        """Test listing workflow versions."""
        mock_catalog = MagicMock()
        mock_version1 = MagicMock()
        mock_version1.id = "wf-v1"
        mock_version1.name = "test_workflow"
        mock_version1.version = 1

        mock_version2 = MagicMock()
        mock_version2.id = "wf-v2"
        mock_version2.name = "test_workflow"
        mock_version2.version = 2

        mock_catalog.versions.return_value = [mock_version2, mock_version1]
        mock_catalog_create.return_value = mock_catalog

        response = test_client.get("/workflows/test_workflow/versions")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["version"] == 2
        assert data[1]["version"] == 1

    @patch("flux.server.WorkflowCatalog.create")
    def test_workflow_versions_empty(self, mock_catalog_create, test_client):
        """Test listing versions for non-existent workflow returns 404."""
        mock_catalog = MagicMock()
        mock_catalog.versions.return_value = []
        mock_catalog_create.return_value = mock_catalog

        response = test_client.get("/workflows/nonexistent/versions")

        # The endpoint returns 404 when no versions exist
        assert response.status_code == 404
        assert "not found" in response.text.lower()

    @patch("flux.server.WorkflowCatalog.create")
    def test_workflow_version_get(self, mock_catalog_create, test_client):
        """Test getting a specific workflow version."""
        mock_catalog = MagicMock()
        mock_workflow = MagicMock()
        mock_workflow.id = "wf-123"
        mock_workflow.name = "test_workflow"
        mock_workflow.version = 2
        mock_workflow.imports = ["flux"]
        mock_workflow.source = b"test source"
        mock_workflow.requests = None
        # Mock to_dict since the endpoint returns workflow.to_dict()
        mock_workflow.to_dict.return_value = {
            "id": "wf-123",
            "name": "test_workflow",
            "version": 2,
            "imports": ["flux"],
            "source": "dGVzdCBzb3VyY2U=",  # base64 encoded
        }

        mock_catalog.get.return_value = mock_workflow
        mock_catalog_create.return_value = mock_catalog

        response = test_client.get("/workflows/test_workflow/versions/2")

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "test_workflow"
        assert data["version"] == 2
        mock_catalog.get.assert_called_once_with("test_workflow", 2)

    @patch("flux.server.WorkflowCatalog.create")
    def test_workflow_version_not_found(self, mock_catalog_create, test_client):
        """Test getting a non-existent workflow version raises WorkflowNotFoundError."""
        from flux.errors import WorkflowNotFoundError

        mock_catalog = MagicMock()
        mock_catalog.get.side_effect = WorkflowNotFoundError("test_workflow", 99)
        mock_catalog_create.return_value = mock_catalog

        response = test_client.get("/workflows/test_workflow/versions/99")

        assert response.status_code == 404
        assert "not found" in response.text.lower()


class TestExecutionEndpoints:
    """Tests for execution listing endpoints."""

    @patch("flux.server.ContextManager.create")
    def test_executions_list(self, mock_cm_create, test_client):
        """Test listing all executions."""
        mock_cm = MagicMock()
        mock_exec = MagicMock()
        mock_exec.execution_id = "exec-123"
        mock_exec.workflow_id = "wf-123"
        mock_exec.workflow_name = "test_workflow"
        mock_exec.state = ExecutionState.COMPLETED
        mock_exec.current_worker = "worker-1"  # The endpoint uses current_worker

        mock_cm.list.return_value = ([mock_exec], 1)
        mock_cm_create.return_value = mock_cm

        response = test_client.get("/executions")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["executions"]) == 1
        assert data["executions"][0]["execution_id"] == "exec-123"

    @patch("flux.server.ContextManager.create")
    def test_executions_list_with_filters(self, mock_cm_create, test_client):
        """Test listing executions with filters."""
        mock_cm = MagicMock()
        mock_cm.list.return_value = ([], 0)
        mock_cm_create.return_value = mock_cm

        response = test_client.get("/executions?workflow_name=test&state=running&limit=10&offset=5")

        assert response.status_code == 200
        data = response.json()
        assert data["limit"] == 10
        assert data["offset"] == 5
        mock_cm.list.assert_called_once()

    @patch("flux.server.ContextManager.create")
    def test_execution_get(self, mock_cm_create, test_client):
        """Test getting a specific execution."""
        mock_cm = MagicMock()
        mock_exec = MagicMock()
        mock_exec.execution_id = "exec-123"
        mock_exec.workflow_id = "wf-123"
        mock_exec.workflow_name = "test_workflow"
        mock_exec.state = ExecutionState.COMPLETED
        mock_exec.worker_name = "worker-1"
        mock_exec.to_dict.return_value = {
            "execution_id": "exec-123",
            "workflow_name": "test_workflow",
            "state": "COMPLETED",
        }

        mock_cm.get.return_value = mock_exec
        mock_cm_create.return_value = mock_cm

        response = test_client.get("/executions/exec-123")

        assert response.status_code == 200
        data = response.json()
        assert data["execution_id"] == "exec-123"
        mock_cm.get.assert_called_once_with("exec-123")

    @patch("flux.server.ContextManager.create")
    def test_execution_not_found(self, mock_cm_create, test_client):
        """Test getting a non-existent execution."""
        mock_cm = MagicMock()
        mock_cm.get.return_value = None
        mock_cm_create.return_value = mock_cm

        response = test_client.get("/executions/nonexistent")

        assert response.status_code == 404
        assert "not found" in response.text.lower()

    @patch("flux.server.ContextManager.create")
    @patch("flux.server.WorkflowCatalog.create")
    def test_workflow_executions_list(self, mock_catalog_create, mock_cm_create, test_client):
        """Test listing executions for a specific workflow."""
        # Mock workflow catalog - endpoint checks if workflow exists first
        mock_catalog = MagicMock()
        mock_workflow = MagicMock()
        mock_catalog.get.return_value = mock_workflow
        mock_catalog_create.return_value = mock_catalog

        # Mock context manager for listing executions
        mock_cm = MagicMock()
        mock_exec = MagicMock()
        mock_exec.execution_id = "exec-123"
        mock_exec.workflow_id = "wf-123"
        mock_exec.workflow_name = "test_workflow"
        mock_exec.state = ExecutionState.RUNNING
        mock_exec.current_worker = "worker-1"  # The endpoint uses current_worker

        mock_cm.list.return_value = ([mock_exec], 1)
        mock_cm_create.return_value = mock_cm

        response = test_client.get("/workflows/test_workflow/executions")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["executions"][0]["workflow_name"] == "test_workflow"


class TestWorkerEndpoints:
    """Tests for worker management endpoints."""

    @patch("flux.server.WorkerRegistry.create")
    def test_workers_list(self, mock_registry_create, test_client):
        """Test listing all workers."""
        mock_registry = MagicMock()
        mock_worker = MagicMock()
        mock_worker.name = "worker-1"
        mock_worker.runtime = None
        mock_worker.resources = None
        mock_worker.packages = []

        mock_registry.list.return_value = [mock_worker]
        mock_registry_create.return_value = mock_registry

        response = test_client.get("/workers")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "worker-1"

    @patch("flux.server.WorkerRegistry.create")
    def test_workers_list_empty(self, mock_registry_create, test_client):
        """Test listing workers when none exist."""
        mock_registry = MagicMock()
        mock_registry.list.return_value = []
        mock_registry_create.return_value = mock_registry

        response = test_client.get("/workers")

        assert response.status_code == 200
        data = response.json()
        assert data == []

    @patch("flux.server.WorkerRegistry.create")
    def test_worker_get(self, mock_registry_create, test_client):
        """Test getting a specific worker."""
        mock_registry = MagicMock()
        mock_worker = MagicMock()
        mock_worker.name = "worker-1"
        # The endpoint expects runtime with os_name, os_version, python_version
        mock_worker.runtime = MagicMock()
        mock_worker.runtime.os_name = "Linux"
        mock_worker.runtime.os_version = "5.4.0"
        mock_worker.runtime.python_version = "3.12.0"
        mock_worker.resources = None
        mock_worker.packages = [{"name": "numpy", "version": "1.24.0"}]

        mock_registry.get.return_value = mock_worker
        mock_registry_create.return_value = mock_registry

        response = test_client.get("/workers/worker-1")

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "worker-1"
        mock_registry.get.assert_called_once_with("worker-1")

    @patch("flux.server.WorkerRegistry.create")
    def test_worker_not_found(self, mock_registry_create, test_client):
        """Test getting a non-existent worker."""
        mock_registry = MagicMock()
        mock_registry.get.side_effect = WorkerNotFoundError("nonexistent")
        mock_registry_create.return_value = mock_registry

        response = test_client.get("/workers/nonexistent")

        assert response.status_code == 404
        assert "not found" in response.text.lower()


class TestHealthEndpoint:
    """Tests for health check endpoint."""

    @patch("flux.server.WorkflowCatalog.create")
    def test_health_check_healthy(self, mock_catalog_create, test_client):
        """Test health check when database is healthy."""
        mock_catalog = MagicMock()
        mock_catalog.health_check.return_value = True
        mock_catalog_create.return_value = mock_catalog

        response = test_client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["database"] is True
        assert "version" in data

    @patch("flux.server.WorkflowCatalog.create")
    def test_health_check_unhealthy(self, mock_catalog_create, test_client):
        """Test health check when database is unhealthy."""
        mock_catalog = MagicMock()
        mock_catalog.health_check.return_value = False
        mock_catalog_create.return_value = mock_catalog

        response = test_client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "unhealthy"
        assert data["database"] is False

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


class TestAPIEdgeCases:
    """Tests for edge cases and error handling in API endpoints."""

    @patch("flux.server.ContextManager.create")
    def test_executions_list_invalid_state(self, mock_cm_create, test_client):
        """Test listing executions with invalid state returns 400."""
        mock_cm = MagicMock()
        mock_cm_create.return_value = mock_cm

        response = test_client.get("/executions?state=INVALID_STATE")

        assert response.status_code == 400
        assert "invalid state" in response.text.lower()

    @patch("flux.server.WorkflowCatalog.create")
    def test_workflow_delete_handles_exception(self, mock_catalog_create, test_client):
        """Test workflow delete handles database errors gracefully."""
        mock_catalog = MagicMock()
        mock_catalog.delete.side_effect = Exception("Database connection failed")
        mock_catalog_create.return_value = mock_catalog

        response = test_client.delete("/workflows/test_workflow")

        assert response.status_code == 500
        assert "error" in response.text.lower()

    @patch("flux.server.WorkflowCatalog.create")
    def test_health_check_handles_exception(self, mock_catalog_create, test_client):
        """Test health check returns unhealthy on exception."""
        mock_catalog = MagicMock()
        mock_catalog.health_check.side_effect = Exception("Connection failed")
        mock_catalog_create.return_value = mock_catalog

        response = test_client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "unhealthy"

    @patch("flux.server.ContextManager.create")
    def test_executions_list_handles_exception(self, mock_cm_create, test_client):
        """Test executions list handles errors gracefully."""
        mock_cm = MagicMock()
        mock_cm.list.side_effect = Exception("Database error")
        mock_cm_create.return_value = mock_cm

        response = test_client.get("/executions")

        assert response.status_code == 500
        assert "error" in response.text.lower()

    @patch("flux.server.WorkerRegistry.create")
    def test_workers_list_handles_exception(self, mock_registry_create, test_client):
        """Test workers list handles errors gracefully."""
        mock_registry = MagicMock()
        mock_registry.list.side_effect = Exception("Registry unavailable")
        mock_registry_create.return_value = mock_registry

        response = test_client.get("/workers")

        assert response.status_code == 500
        assert "error" in response.text.lower()

    @patch("flux.server.WorkflowCatalog.create")
    def test_workflow_versions_handles_exception(self, mock_catalog_create, test_client):
        """Test workflow versions handles errors gracefully."""
        mock_catalog = MagicMock()
        mock_catalog.versions.side_effect = Exception("Database error")
        mock_catalog_create.return_value = mock_catalog

        response = test_client.get("/workflows/test_workflow/versions")

        assert response.status_code == 500
        assert "error" in response.text.lower()

    @patch("flux.server.ContextManager.create")
    @patch("flux.server.WorkflowCatalog.create")
    def test_workflow_executions_not_found(self, mock_catalog_create, mock_cm_create, test_client):
        """Test listing executions for non-existent workflow returns 404."""
        from flux.errors import WorkflowNotFoundError

        mock_catalog = MagicMock()
        mock_catalog.get.side_effect = WorkflowNotFoundError("nonexistent")
        mock_catalog_create.return_value = mock_catalog

        response = test_client.get("/workflows/nonexistent/executions")

        assert response.status_code == 404
        assert "not found" in response.text.lower()

    @patch("flux.server.ContextManager.create")
    @patch("flux.server.WorkflowCatalog.create")
    def test_workflow_executions_invalid_state(
        self,
        mock_catalog_create,
        mock_cm_create,
        test_client,
    ):
        """Test listing workflow executions with invalid state returns 400."""
        mock_catalog = MagicMock()
        mock_workflow = MagicMock()
        mock_catalog.get.return_value = mock_workflow
        mock_catalog_create.return_value = mock_catalog

        response = test_client.get("/workflows/test_workflow/executions?state=BAD")

        assert response.status_code == 400
        assert "invalid state" in response.text.lower()


class TestWorkflowRunWithVersion:
    """Tests for running workflows with specific version."""

    @patch("flux.server.ContextManager.create")
    @patch("flux.server.WorkflowCatalog.create")
    def test_run_workflow_latest_version(self, mock_catalog_create, mock_cm_create, test_client):
        """Test running workflow without version uses latest."""
        # Setup workflow mock
        mock_catalog = MagicMock()
        mock_workflow = MagicMock()
        mock_workflow.id = "wf-123"
        mock_workflow.name = "test_workflow"
        mock_workflow.requests = None
        mock_catalog.get.return_value = mock_workflow
        mock_catalog_create.return_value = mock_catalog

        # Setup context mock with proper ExecutionState
        mock_cm = MagicMock()
        mock_ctx = MagicMock(spec=ExecutionContext)
        mock_ctx.execution_id = "exec-123"
        mock_ctx.workflow_id = "wf-123"
        mock_ctx.workflow_name = "test_workflow"
        mock_ctx.input = None
        mock_ctx.output = None
        mock_ctx.state = ExecutionState.CREATED
        mock_ctx.events = []
        mock_ctx.has_finished = False
        mock_cm.save.return_value = mock_ctx
        mock_cm_create.return_value = mock_cm

        response = test_client.post("/workflows/test_workflow/run/async")

        assert response.status_code == 200
        # Verify catalog.get was called without version (None)
        mock_catalog.get.assert_called_once_with("test_workflow", None)

    @patch("flux.server.ContextManager.create")
    @patch("flux.server.WorkflowCatalog.create")
    def test_run_workflow_specific_version(self, mock_catalog_create, mock_cm_create, test_client):
        """Test running workflow with specific version."""
        # Setup workflow mock
        mock_catalog = MagicMock()
        mock_workflow = MagicMock()
        mock_workflow.id = "wf-123-v2"
        mock_workflow.name = "test_workflow"
        mock_workflow.version = 2
        mock_workflow.requests = None
        mock_catalog.get.return_value = mock_workflow
        mock_catalog_create.return_value = mock_catalog

        # Setup context mock with proper ExecutionState
        mock_cm = MagicMock()
        mock_ctx = MagicMock(spec=ExecutionContext)
        mock_ctx.execution_id = "exec-456"
        mock_ctx.workflow_id = "wf-123-v2"
        mock_ctx.workflow_name = "test_workflow"
        mock_ctx.input = None
        mock_ctx.output = None
        mock_ctx.state = ExecutionState.CREATED
        mock_ctx.events = []
        mock_ctx.has_finished = False
        mock_cm.save.return_value = mock_ctx
        mock_cm_create.return_value = mock_cm

        response = test_client.post("/workflows/test_workflow/run/async?version=2")

        assert response.status_code == 200
        # Verify catalog.get was called with version=2
        mock_catalog.get.assert_called_once_with("test_workflow", 2)

    @patch("flux.server.ContextManager.create")
    @patch("flux.server.WorkflowCatalog.create")
    def test_run_workflow_version_not_found(self, mock_catalog_create, mock_cm_create, test_client):
        """Test running workflow with non-existent version returns 404."""
        from flux.errors import WorkflowNotFoundError

        mock_catalog = MagicMock()
        mock_catalog.get.side_effect = WorkflowNotFoundError("test_workflow", 99)
        mock_catalog_create.return_value = mock_catalog

        response = test_client.post("/workflows/test_workflow/run/async?version=99")

        assert response.status_code == 404
        assert "not found" in response.text.lower()
