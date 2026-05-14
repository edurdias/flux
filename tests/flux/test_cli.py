"""Tests for the Flux CLI commands."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from flux.cli import cli


@pytest.fixture
def runner():
    """Create a CLI runner for testing."""
    return CliRunner()


@pytest.fixture
def mock_httpx_client():
    """Create a mock httpx client context manager."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_client.get.return_value = mock_response
    mock_client.post.return_value = mock_response
    mock_client.delete.return_value = mock_response
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    return mock_client, mock_response


# =============================================================================
# Workflow Delete Tests
# =============================================================================


class TestWorkflowDelete:
    """Tests for workflow delete command."""

    @patch("flux.cli.httpx.Client")
    def test_delete_workflow_success(self, mock_client_class, runner):
        """Test successful workflow deletion."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"status": "success"}
        mock_client.delete.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["workflow", "delete", "test_workflow", "--force"])

        assert result.exit_code == 0
        assert "Successfully deleted" in result.output
        mock_client.delete.assert_called_once()

    @patch("flux.cli.httpx.Client")
    def test_delete_workflow_specific_version(self, mock_client_class, runner):
        """Test deleting a specific workflow version."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client.delete.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(
            cli,
            ["workflow", "delete", "test_workflow", "--version", "2", "--force"],
        )

        assert result.exit_code == 0
        assert "version 2" in result.output
        # Verify version parameter was passed
        call_kwargs = mock_client.delete.call_args
        assert call_kwargs[1]["params"]["version"] == 2

    @patch("flux.cli.httpx.Client")
    def test_delete_workflow_not_found(self, mock_client_class, runner):
        """Test deleting a non-existent workflow."""
        import httpx

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 404
        error = httpx.HTTPStatusError("Not found", request=MagicMock(), response=mock_response)
        mock_response.raise_for_status.side_effect = error
        mock_client.delete.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["workflow", "delete", "nonexistent", "--force"])

        assert "not found" in result.output.lower()

    def test_delete_workflow_cancelled(self, runner):
        """Test cancelling workflow deletion."""
        result = runner.invoke(cli, ["workflow", "delete", "test_workflow"], input="n\n")

        assert result.exit_code == 0
        assert "Cancelled" in result.output


# =============================================================================
# Workflow Versions Tests
# =============================================================================


class TestWorkflowVersions:
    """Tests for workflow versions command."""

    @patch("flux.cli.httpx.Client")
    def test_list_versions_success(self, mock_client_class, runner):
        """Test listing workflow versions."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [
            {"id": "wf-v3", "name": "test_workflow", "version": 3},
            {"id": "wf-v2", "name": "test_workflow", "version": 2},
            {"id": "wf-v1", "name": "test_workflow", "version": 1},
        ]
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["workflow", "versions", "test_workflow"])

        assert result.exit_code == 0
        assert "Version 3" in result.output
        assert "Version 2" in result.output
        assert "Version 1" in result.output

    @patch("flux.cli.httpx.Client")
    def test_list_versions_empty(self, mock_client_class, runner):
        """Test listing versions for workflow with no versions."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = []
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["workflow", "versions", "test_workflow"])

        assert result.exit_code == 0
        assert "No versions found" in result.output

    @patch("flux.cli.httpx.Client")
    def test_list_versions_json_format(self, mock_client_class, runner):
        """Test listing versions in JSON format."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [
            {"id": "wf-v1", "name": "test_workflow", "version": 1},
        ]
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["workflow", "versions", "test_workflow", "-f", "json"])

        assert result.exit_code == 0
        assert '"version": 1' in result.output


# =============================================================================
# Workflow Run with Version Tests
# =============================================================================


class TestWorkflowRunWithVersion:
    """Tests for workflow run command with version parameter."""

    @patch("flux.cli.httpx.Client")
    def test_run_workflow_with_version(self, mock_client_class, runner):
        """Test running a specific workflow version."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "execution_id": "exec-123",
            "state": "CREATED",
        }
        mock_client.post.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(
            cli,
            ["workflow", "run", "test_workflow", '{"key": "value"}', "--version", "2"],
        )

        assert result.exit_code == 0
        # Verify version parameter was passed
        call_kwargs = mock_client.post.call_args
        assert call_kwargs[1]["params"]["version"] == 2

    @patch("flux.cli.httpx.Client")
    def test_run_workflow_without_version(self, mock_client_class, runner):
        """Test running workflow without version uses latest."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "execution_id": "exec-123",
            "state": "CREATED",
        }
        mock_client.post.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["workflow", "run", "test_workflow", '{"key": "value"}'])

        assert result.exit_code == 0
        # Verify version parameter was not passed
        call_kwargs = mock_client.post.call_args
        assert "version" not in call_kwargs[1]["params"]


# =============================================================================
# Workflow Show with Version Tests
# =============================================================================


class TestWorkflowShowWithVersion:
    """Tests for workflow show command with version parameter."""

    @patch("flux.cli.httpx.Client")
    def test_show_workflow_with_version(self, mock_client_class, runner):
        """Test showing a specific workflow version."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "id": "wf-123",
            "name": "test_workflow",
            "version": 2,
        }
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["workflow", "show", "test_workflow", "--version", "2"])

        assert result.exit_code == 0
        # Verify correct URL was called
        call_args = mock_client.get.call_args
        assert "/versions/2" in call_args[0][0]

    @patch("flux.cli.httpx.Client")
    def test_show_workflow_without_version(self, mock_client_class, runner):
        """Test showing workflow without version shows latest."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "id": "wf-123",
            "name": "test_workflow",
            "version": 3,
        }
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["workflow", "show", "test_workflow"])

        assert result.exit_code == 0
        # Verify URL does not contain /versions/
        call_args = mock_client.get.call_args
        assert "/versions/" not in call_args[0][0]


# =============================================================================
# Execution List Tests
# =============================================================================


class TestExecutionList:
    """Tests for execution list command."""

    @patch("flux.cli.httpx.Client")
    def test_list_executions_success(self, mock_client_class, runner):
        """Test listing executions."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "executions": [
                {
                    "execution_id": "exec-123",
                    "workflow_name": "test_workflow",
                    "state": "COMPLETED",
                    "worker_name": "worker-1",
                },
                {
                    "execution_id": "exec-456",
                    "workflow_name": "test_workflow",
                    "state": "RUNNING",
                    "worker_name": "worker-2",
                },
            ],
            "total": 2,
            "limit": 50,
            "offset": 0,
        }
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["execution", "list"])

        assert result.exit_code == 0
        assert "exec-123" in result.output
        assert "COMPLETED" in result.output

    @patch("flux.cli.httpx.Client")
    def test_list_executions_with_filters(self, mock_client_class, runner):
        """Test listing executions with filters."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "executions": [],
            "total": 0,
            "limit": 10,
            "offset": 5,
        }
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(
            cli,
            [
                "execution",
                "list",
                "--workflow",
                "test",
                "--state",
                "RUNNING",
                "--limit",
                "10",
                "--offset",
                "5",
            ],
        )

        assert result.exit_code == 0
        # Verify filters were passed
        call_kwargs = mock_client.get.call_args
        params = call_kwargs[1]["params"]
        assert params["workflow_name"] == "test"
        assert params["state"] == "RUNNING"
        assert params["limit"] == 10
        assert params["offset"] == 5

    @patch("flux.cli.httpx.Client")
    def test_list_executions_empty(self, mock_client_class, runner):
        """Test listing executions when none exist."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "executions": [],
            "total": 0,
            "limit": 50,
            "offset": 0,
        }
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["execution", "list"])

        assert result.exit_code == 0
        assert "No executions found" in result.output

    @patch("flux.cli.httpx.Client")
    def test_list_executions_json_format(self, mock_client_class, runner):
        """Test listing executions in JSON format."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "executions": [{"execution_id": "exec-123", "state": "COMPLETED"}],
            "total": 1,
        }
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["execution", "list", "-f", "json"])

        assert result.exit_code == 0
        assert '"execution_id": "exec-123"' in result.output


# =============================================================================
# Execution Show Tests
# =============================================================================


class TestExecutionShow:
    """Tests for execution show command."""

    @patch("flux.cli.httpx.Client")
    def test_show_execution_success(self, mock_client_class, runner):
        """Test showing execution details."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "execution_id": "exec-123",
            "workflow_name": "test_workflow",
            "state": "COMPLETED",
            "output": {"result": "success"},
        }
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["execution", "show", "exec-123"])

        assert result.exit_code == 0
        assert "exec-123" in result.output

    @patch("flux.cli.httpx.Client")
    def test_show_execution_not_found(self, mock_client_class, runner):
        """Test showing non-existent execution."""
        import httpx

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 404
        error = httpx.HTTPStatusError("Not found", request=MagicMock(), response=mock_response)
        mock_response.raise_for_status.side_effect = error
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["execution", "show", "nonexistent"])

        assert "not found" in result.output.lower()


# =============================================================================
# Worker List Tests
# =============================================================================


class TestWorkerList:
    """Tests for worker list command."""

    @patch("flux.cli.httpx.Client")
    def test_list_workers_success(self, mock_client_class, runner):
        """Test listing workers."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [
            {
                "name": "worker-1",
                "runtime": {"python_version": "3.12.0"},
            },
            {
                "name": "worker-2",
                "runtime": {"python_version": "3.11.0"},
            },
        ]
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["worker", "list"])

        assert result.exit_code == 0
        assert "worker-1" in result.output
        assert "worker-2" in result.output
        assert "3.12.0" in result.output

    @patch("flux.cli.httpx.Client")
    def test_list_workers_empty(self, mock_client_class, runner):
        """Test listing workers when none exist."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = []
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["worker", "list"])

        assert result.exit_code == 0
        assert "No workers found" in result.output

    @patch("flux.cli.httpx.Client")
    def test_list_workers_json_format(self, mock_client_class, runner):
        """Test listing workers in JSON format."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [{"name": "worker-1", "runtime": None}]
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["worker", "list", "-f", "json"])

        assert result.exit_code == 0
        assert '"name": "worker-1"' in result.output


# =============================================================================
# Worker Show Tests
# =============================================================================


class TestWorkerShow:
    """Tests for worker show command."""

    @patch("flux.cli.httpx.Client")
    def test_show_worker_success(self, mock_client_class, runner):
        """Test showing worker details."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "name": "worker-1",
            "runtime": {
                "os_name": "Linux",
                "python_version": "3.12.0",
            },
            "packages": [{"name": "numpy", "version": "1.24.0"}],
        }
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["worker", "show", "worker-1"])

        assert result.exit_code == 0
        assert "worker-1" in result.output

    @patch("flux.cli.httpx.Client")
    def test_show_worker_not_found(self, mock_client_class, runner):
        """Test showing non-existent worker."""
        import httpx

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 404
        error = httpx.HTTPStatusError("Not found", request=MagicMock(), response=mock_response)
        mock_response.raise_for_status.side_effect = error
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["worker", "show", "nonexistent"])

        assert "not found" in result.output.lower()


# =============================================================================
# Health Command Tests
# =============================================================================


class TestHealthCommand:
    """Tests for health command."""

    @patch("flux.cli.httpx.Client")
    def test_health_check_healthy(self, mock_client_class, runner):
        """Test health check when server is healthy."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "status": "healthy",
            "database": True,
            "version": "1.0.0",
        }
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["health"])

        assert result.exit_code == 0
        assert "healthy" in result.output.lower()
        assert "connected" in result.output.lower()
        assert "1.0.0" in result.output

    @patch("flux.cli.httpx.Client")
    def test_health_check_unhealthy(self, mock_client_class, runner):
        """Test health check when server is unhealthy."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "status": "unhealthy",
            "database": False,
            "version": "1.0.0",
        }
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["health"])

        assert "unhealthy" in result.output.lower()
        assert "disconnected" in result.output.lower()

    @patch("flux.cli.httpx.Client")
    def test_health_check_connection_error(self, mock_client_class, runner):
        """Test health check when server is unreachable."""
        import httpx

        mock_client = MagicMock()
        mock_client.get.side_effect = httpx.ConnectError("Connection refused")
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["health"])

        assert "Cannot connect" in result.output


# =============================================================================
# Namespace-Aware CLI Tests
# =============================================================================


class TestWorkflowShowNamespace:
    """Tests for namespace-aware workflow show command."""

    @patch("flux.cli.httpx.Client")
    def test_cli_show_accepts_qualified_ref(self, mock_client_class, runner):
        """flux workflow show billing/invoice -> GET /workflows/billing/invoice"""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "id": "wf-123",
            "name": "invoice",
            "namespace": "billing",
            "version": 1,
        }
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["workflow", "show", "billing/invoice"])

        assert result.exit_code == 0
        call_args = mock_client.get.call_args
        assert "/workflows/billing/invoice" in call_args[0][0]

    @patch("flux.cli.httpx.Client")
    def test_cli_show_bare_name_resolves_to_default(self, mock_client_class, runner):
        """flux workflow show hello -> GET /workflows/default/hello"""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "id": "wf-456",
            "name": "hello",
            "namespace": "default",
            "version": 1,
        }
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["workflow", "show", "hello"])

        assert result.exit_code == 0
        call_args = mock_client.get.call_args
        assert "/workflows/default/hello" in call_args[0][0]


class TestWorkflowListNamespaces:
    """Tests for list-namespaces command."""

    @patch("flux.cli.httpx.Client")
    def test_cli_list_namespaces_command(self, mock_client_class, runner):
        """flux workflow list-namespaces -> GET /namespaces"""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [
            {"namespace": "default", "workflow_count": 3},
            {"namespace": "billing", "workflow_count": 1},
        ]
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["workflow", "list-namespaces"])

        assert result.exit_code == 0
        call_args = mock_client.get.call_args
        assert call_args[0][0].endswith("/namespaces")
        assert "default" in result.output
        assert "billing" in result.output

    @patch("flux.cli.httpx.Client")
    def test_cli_list_namespaces_json_format(self, mock_client_class, runner):
        """flux workflow list-namespaces -f json returns JSON output."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [
            {"namespace": "default", "workflow_count": 2},
        ]
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["workflow", "list-namespaces", "-f", "json"])

        assert result.exit_code == 0
        assert '"namespace"' in result.output
        assert '"workflow_count"' in result.output

    @patch("flux.cli.httpx.Client")
    def test_cli_list_namespaces_empty(self, mock_client_class, runner):
        """flux workflow list-namespaces prints message when no namespaces exist."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = []
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["workflow", "list-namespaces"])

        assert result.exit_code == 0
        assert "No namespaces found" in result.output


class TestWorkflowListNamespaceFilter:
    """Tests for --namespace filter on workflow list command."""

    @patch("flux.cli.httpx.Client")
    def test_cli_list_workflows_namespace_filter(self, mock_client_class, runner):
        """flux workflow list --namespace billing -> GET /workflows?namespace=billing"""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [
            {"name": "invoice", "namespace": "billing", "version": 1},
        ]
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["workflow", "list", "--namespace", "billing"])

        assert result.exit_code == 0
        call_args = mock_client.get.call_args
        assert call_args[1]["params"] == {"namespace": "billing"}
        assert "billing/invoice" in result.output

    @patch("flux.cli.httpx.Client")
    def test_cli_list_workflows_no_namespace_filter(self, mock_client_class, runner):
        """flux workflow list without --namespace sends no namespace param."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [
            {"name": "hello", "namespace": "default", "version": 1},
        ]
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["workflow", "list"])

        assert result.exit_code == 0
        call_args = mock_client.get.call_args
        assert call_args[1]["params"] is None
        assert "default/hello" in result.output

    @patch("flux.cli.httpx.Client")
    def test_cli_list_workflows_displays_namespace_name(self, mock_client_class, runner):
        """Workflow list output shows namespace/name format."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [
            {"name": "process", "namespace": "orders", "version": 2},
        ]
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["workflow", "list"])

        assert result.exit_code == 0
        assert "orders/process" in result.output


class TestWorkflowCommandsNamespaceRouting:
    """Tests verifying all workflow commands route to namespace-aware URLs."""

    @patch("flux.cli.httpx.Client")
    def test_delete_uses_namespace_url(self, mock_client_class, runner):
        """flux workflow delete billing/invoice -> DELETE /workflows/billing/invoice"""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client.delete.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["workflow", "delete", "billing/invoice", "--force"])

        assert result.exit_code == 0
        call_args = mock_client.delete.call_args
        assert "/workflows/billing/invoice" in call_args[0][0]

    @patch("flux.cli.httpx.Client")
    def test_versions_uses_namespace_url(self, mock_client_class, runner):
        """flux workflow versions billing/invoice -> GET /workflows/billing/invoice/versions"""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [{"id": "v1", "version": 1}]
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["workflow", "versions", "billing/invoice"])

        assert result.exit_code == 0
        call_args = mock_client.get.call_args
        assert "/workflows/billing/invoice/versions" in call_args[0][0]

    @patch("flux.cli.httpx.Client")
    def test_run_uses_namespace_url(self, mock_client_class, runner):
        """flux workflow run billing/invoice '{}' -> POST /workflows/billing/invoice/run/async"""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"execution_id": "exec-1", "state": "CREATED"}
        mock_client.post.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["workflow", "run", "billing/invoice", "{}"])

        assert result.exit_code == 0
        call_args = mock_client.post.call_args
        assert "/workflows/billing/invoice/run/async" in call_args[0][0]

    @patch("flux.cli.httpx.Client")
    def test_status_uses_namespace_url(self, mock_client_class, runner):
        """flux workflow status billing/invoice exec-1 -> GET /workflows/billing/invoice/status/exec-1"""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"execution_id": "exec-1", "state": "COMPLETED"}
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["workflow", "status", "billing/invoice", "exec-1"])

        assert result.exit_code == 0
        call_args = mock_client.get.call_args
        assert "/workflows/billing/invoice/status/exec-1" in call_args[0][0]
