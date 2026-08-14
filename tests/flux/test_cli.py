"""Tests for the Flux CLI commands."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

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
    def test_list_executions_shows_the_operator_name(self, mock_client_class, runner):
        """A named execution is named in the listing -- otherwise
        `flux execution rename` writes a label nothing can read back."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "executions": [
                {
                    "execution_id": "exec-123",
                    "workflow_name": "test_workflow",
                    "state": "RUNNING",
                    "worker_name": "worker-1",
                    "name": "nightly reprocess",
                },
                {
                    "execution_id": "exec-456",
                    "workflow_name": "test_workflow",
                    "state": "RUNNING",
                    "worker_name": "worker-2",
                    "name": None,
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
        assert '"nightly reprocess"' in result.output
        # An unnamed execution spends no width saying so.
        rows = [line for line in result.output.splitlines() if "exec-456" in line]
        assert rows and '"' not in rows[0]

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
# Workflow Run --name Tests
# =============================================================================


class TestWorkflowRunWithName:
    """Tests for the --name option on `workflow run`."""

    @patch("flux.cli.httpx.Client")
    def test_name_passed_as_query_param(self, mock_client_class, runner):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"execution_id": "exec-123", "state": "CREATED"}
        mock_client.post.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(
            cli,
            ["workflow", "run", "test_workflow", '{"key": "value"}', "--name", "fix CI"],
        )

        assert result.exit_code == 0, result.output
        call_kwargs = mock_client.post.call_args
        assert call_kwargs[1]["params"]["name"] == "fix CI"

    @patch("flux.cli.httpx.Client")
    def test_no_name_omits_the_param(self, mock_client_class, runner):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"execution_id": "exec-123", "state": "CREATED"}
        mock_client.post.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["workflow", "run", "test_workflow", '{"key": "value"}'])

        assert result.exit_code == 0
        call_kwargs = mock_client.post.call_args
        assert "name" not in call_kwargs[1]["params"]


# =============================================================================
# Execution Rename Tests
# =============================================================================


class TestExecutionRename:
    """Tests for `execution rename`."""

    @patch("flux.cli.httpx.Client")
    def test_rename_success(self, mock_client_class, runner):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"execution_id": "exec-123", "name": "renamed"}
        mock_client.put.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["execution", "rename", "exec-123", "renamed"])

        assert result.exit_code == 0, result.output
        assert "renamed" in result.output
        call_args = mock_client.put.call_args
        assert call_args[0][0].endswith("/executions/exec-123/name")
        assert call_args[1]["json"] == {"name": "renamed"}

    @patch("flux.cli.httpx.Client")
    def test_rename_missing_execution(self, mock_client_class, runner):
        import httpx

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 404
        error = httpx.HTTPStatusError("Not found", request=MagicMock(), response=mock_response)
        mock_response.raise_for_status.side_effect = error
        mock_client.put.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        result = runner.invoke(cli, ["execution", "rename", "nope", "x"])

        assert result.exit_code != 0
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
        # First GET fetches the workflow status; a follow-up GET fetches
        # pending approvals for the execution. Inspect the first call.
        first_call = mock_client.get.call_args_list[0]
        assert "/workflows/billing/invoice/status/exec-1" in first_call[0][0]


class TestWorkerLabelSources:
    """--label and [flux.workers] labels compose; the flag wins per key
    (issue #235: config labels used to be silently dropped)."""

    def _start(self, runner, args, config_labels):
        from flux.config import Configuration

        Configuration.get().override(workers={"labels": config_labels})
        try:
            with patch("flux.worker.Worker") as worker_cls:
                worker_cls.return_value.start = MagicMock()
                result = runner.invoke(cli, ["start", "worker", "w1", *args])
                assert result.exit_code == 0, result.output
                return worker_cls.call_args.kwargs["labels"]
        finally:
            Configuration.get().override(workers={"labels": {}})

    def test_config_labels_reach_the_worker(self, runner):
        labels = self._start(runner, [], {"node": "node-a"})
        assert labels == {"node": "node-a"}

    def test_flag_wins_on_key_conflict(self, runner):
        labels = self._start(
            runner,
            ["--label", "node=node-b", "--label", "gpu=true"],
            {"node": "node-a", "region": "eu"},
        )
        assert labels == {"node": "node-b", "region": "eu", "gpu": "true"}

    def test_config_labels_are_normalized_like_flags(self, runner):
        labels = self._start(runner, [], {"  node  ": "  node-a  "})
        assert labels == {"node": "node-a"}

    def test_whitespace_only_config_label_fails_fast(self, runner):
        from flux.config import Configuration

        Configuration.get().override(workers={"labels": {"   ": "x"}})
        try:
            with patch("flux.worker.Worker") as worker_cls:
                result = runner.invoke(cli, ["start", "worker", "w1"])
            assert result.exit_code == 1
            assert "Invalid label in [flux.workers] labels" in result.output
            worker_cls.assert_not_called()
        finally:
            Configuration.get().override(workers={"labels": {}})


# =============================================================================
# Agent Start / Resume CLI Semantics (agent-console Task 9)
# =============================================================================


def _no_custom_workflow_client():
    """An httpx.Client double for the `agent start` custom-workflow lookup
    that reports "no such agent def" (404), so `workflow_name` stays the
    default `agent_chat` — irrelevant to these tests, but the lookup runs
    whenever NAME is given and needs a client to not blow up."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_client.get.return_value = mock_response
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    return mock_client


class TestAgentStartCLI:
    """`flux agent start`: NAME is optional, terminal mode opens the console
    (NAME becomes its initial-agent filter), --plain keeps the old
    single-agent REPL, and a NAME-less non-interactive invocation fails
    clearly instead of hanging on a console that needs a terminal.

    ``_console_can_render`` (patched below as ``can_render``) is the single
    signal both this CLI guard and ``AgentProcess`` itself consult (via
    ``flux.agents.process.wants_plain_terminal``) -- see
    ``test_console_can_render_is_gated_on_stdout_not_stdin`` for the
    regression this replaced (the CLI used to check ``sys.stdin`` while
    ``AgentProcess`` checked ``sys.stdout``)."""

    def _invoke(self, runner, args, *, can_render=True):
        with patch("flux.agents.process.AgentProcess") as process_cls:
            process_cls.return_value.run = AsyncMock()
            with patch("flux.cli._console_can_render", return_value=can_render):
                result = runner.invoke(cli, ["agent", "start", *args])
        return result, process_cls

    def test_web_mode_refuses_a_non_loopback_bind_without_allow_remote(self, runner):
        """web mode carries the operator's token in-process, so reaching the
        port IS the authorization -- exposure must be a deliberate act."""
        result, process_cls = self._invoke(runner, ["--mode", "web", "--host", "0.0.0.0"])

        assert result.exit_code == 1
        assert "no authentication of its own" in result.output
        assert "--allow-remote" in result.output and "--mode api" in result.output
        process_cls.assert_not_called()

    def test_allow_remote_permits_the_bind_and_reaches_the_process(self, runner):
        result, process_cls = self._invoke(
            runner,
            ["--mode", "web", "--host", "0.0.0.0", "--allow-remote"],
        )

        assert result.exit_code == 0, result.output
        assert process_cls.call_args.kwargs["allow_remote"] is True

    def test_api_mode_is_exempt_from_the_bind_gate(self, runner):
        """api mode authenticates every request with a Bearer -- remote use
        is its purpose, not an accident."""
        result, process_cls = self._invoke(runner, ["--mode", "api", "--host", "0.0.0.0"])

        assert result.exit_code == 0, result.output
        assert process_cls.call_args.kwargs["allow_remote"] is False

    def test_loopback_binds_need_no_flag(self, runner):
        for host in ("127.0.0.1", "localhost", "::1"):
            result, process_cls = self._invoke(runner, ["--mode", "web", "--host", host])
            assert result.exit_code == 0, f"{host}: {result.output}"
            process_cls.assert_called_once()

    def test_allow_origin_threads_into_the_agent_process(self, runner):
        result, process_cls = self._invoke(
            runner,
            ["--mode", "web", "--allow-origin", "http://console.internal:8080"],
            can_render=True,
        )

        assert result.exit_code == 0, result.output
        kwargs = process_cls.call_args.kwargs
        assert kwargs["allowed_origins"] == ("http://console.internal:8080",)

    def test_name_less_tty_opens_console_with_no_initial_agent(self, runner):
        result, process_cls = self._invoke(runner, [], can_render=True)

        assert result.exit_code == 0, result.output
        kwargs = process_cls.call_args.kwargs
        assert kwargs["agent_name"] is None
        assert kwargs["mode"] == "terminal"

    def test_name_less_non_tty_exits_with_clear_error(self, runner):
        result, process_cls = self._invoke(runner, [], can_render=False)

        assert result.exit_code != 0
        assert (
            "the console needs a terminal; use --mode web/api or provide an agent name"
            in result.output
        )
        process_cls.assert_not_called()

    def test_name_less_non_tty_web_mode_starts_headless(self, runner):
        """A served console renders in a browser, so it must start under
        systemd/Docker/CI where no terminal exists."""
        result, process_cls = self._invoke(runner, ["--mode", "web"], can_render=False)

        assert result.exit_code == 0, result.output
        assert process_cls.call_args.kwargs["mode"] == "web"

    def test_name_less_non_tty_api_mode_is_exempt(self, runner):
        result, process_cls = self._invoke(runner, ["--mode", "api"], can_render=False)

        assert result.exit_code == 0, result.output
        kwargs = process_cls.call_args.kwargs
        assert kwargs["agent_name"] is None
        assert kwargs["mode"] == "api"

    def test_plain_without_name_errors_clearly(self, runner):
        result, process_cls = self._invoke(runner, ["--plain"], can_render=True)

        assert result.exit_code != 0
        assert "--plain requires an agent name" in result.output
        process_cls.assert_not_called()

    def test_plain_with_name_selects_the_plain_repl(self, runner, monkeypatch):
        # The command sets this env var itself (not via monkeypatch), but
        # starting from a known-clean state and letting monkeypatch's own
        # teardown restore it keeps this test from leaking into others.
        monkeypatch.delenv("FLUX_PLAIN_TERMINAL", raising=False)
        with patch("flux.cli.get_http_client", return_value=_no_custom_workflow_client()):
            result, process_cls = self._invoke(runner, ["coder", "--plain"], can_render=True)

        assert result.exit_code == 0, result.output
        assert os.environ.get("FLUX_PLAIN_TERMINAL") == "1"
        kwargs = process_cls.call_args.kwargs
        assert kwargs["agent_name"] == "coder"

    def test_name_terminal_mode_opens_console_focused_on_agent(self, runner, monkeypatch):
        monkeypatch.delenv("FLUX_PLAIN_TERMINAL", raising=False)
        with patch("flux.cli.get_http_client", return_value=_no_custom_workflow_client()):
            result, process_cls = self._invoke(runner, ["coder"], can_render=True)

        assert result.exit_code == 0, result.output
        kwargs = process_cls.call_args.kwargs
        assert kwargs["agent_name"] == "coder"
        assert kwargs["mode"] == "terminal"
        assert "FLUX_PLAIN_TERMINAL" not in os.environ

    def test_session_option_reaches_agent_process(self, runner):
        result, process_cls = self._invoke(
            runner,
            ["--mode", "api", "--session", "exec-1"],
            can_render=False,
        )

        assert result.exit_code == 0, result.output
        kwargs = process_cls.call_args.kwargs
        assert kwargs["session_id"] == "exec-1"

    # --- Fix: composite-signal drift (review finding) --------------------

    def test_console_can_render_is_gated_on_stdout_not_stdin(self):
        """The exact composition the review reproduced: `flux agent start |
        tee log` has an interactive stdin but a redirected stdout. The CLI
        guard must agree with `AgentProcess`'s own decision (stdout-based),
        so an interactive stdin alone must not report "can render".

        Calls `_console_can_render` directly rather than through
        `CliRunner.invoke` -- Click's isolation replaces both `sys.stdin`
        and `sys.stdout` with its own non-tty streams for the duration of
        `invoke()`, so patching the real streams around an `invoke()` call
        would silently test Click's fakes instead of this composition.
        """
        from flux.cli import _console_can_render

        with patch("sys.stdin") as mock_stdin, patch("sys.stdout") as mock_stdout:
            mock_stdin.isatty.return_value = True
            mock_stdout.isatty.return_value = False
            assert _console_can_render() is False

    def test_name_less_real_environment_exits_nonzero_without_mocking(self, runner):
        """No TTY mocking at all: pytest/CliRunner's default streams are
        never a real terminal, so this reproduces the guard firing from the
        exact signal `AgentProcess` itself would consult, with nothing
        faked -- proving the CLI guard and `AgentProcess` cannot disagree
        because there is only one implementation of the check now."""
        result = runner.invoke(cli, ["agent", "start"])

        assert result.exit_code != 0
        assert (
            "the console needs a terminal; use --mode web/api or provide an agent name"
            in result.output
        )

    def test_agent_process_failure_exits_nonzero_not_silently_swallowed(self, runner):
        """Any startup failure (not just the TTY-composite one) must set a
        non-zero exit code -- the bare `except Exception` used to just
        print the message and return, which Click reports as a clean exit
        0, indistinguishable from success to a script or CI caller."""
        with patch("flux.agents.process.AgentProcess", side_effect=RuntimeError("boom")):
            result = runner.invoke(cli, ["agent", "start", "--mode", "api"])

        assert result.exit_code != 0
        assert "Error starting agent: boom" in result.output


class TestAgentSessionResumeCLI:
    """`flux agent session resume <id>` opens the console focused on that
    session (agent-console Task 9), gated by the same `_console_can_render`
    check `agent start` uses -- unguarded, a non-interactive resume
    (piped/scripted/CI) used to hit `AgentProcess`'s `ValueError` (whose
    message references `--plain`, a flag resume doesn't have) and exit 0."""

    def test_resume_opens_console_focused_on_session(self, runner):
        with patch("flux.agents.process.AgentProcess") as process_cls:
            process_cls.return_value.run = AsyncMock()
            with patch("flux.cli._console_can_render", return_value=True):
                result = runner.invoke(cli, ["agent", "session", "resume", "exec-123"])

        assert result.exit_code == 0, result.output
        kwargs = process_cls.call_args.kwargs
        assert kwargs["agent_name"] is None
        assert kwargs["session_id"] == "exec-123"
        assert kwargs["mode"] == "terminal"

    def test_resume_non_interactive_exits_with_clear_error(self, runner):
        with patch("flux.agents.process.AgentProcess") as process_cls:
            with patch("flux.cli._console_can_render", return_value=False):
                result = runner.invoke(cli, ["agent", "session", "resume", "exec-123"])

        assert result.exit_code != 0
        assert "the console needs a terminal to resume a session" in result.output
        assert "--plain" not in result.output
        process_cls.assert_not_called()

    def test_resume_real_environment_exits_nonzero_without_mocking(self, runner):
        """No mocking at all -- pytest/CliRunner's default streams are
        never a real terminal, reproducing the review's "non-interactive
        resume" scenario with nothing faked."""
        result = runner.invoke(cli, ["agent", "session", "resume", "exec-123"])

        assert result.exit_code != 0
        assert "the console needs a terminal to resume a session" in result.output

    def test_resume_process_failure_exits_nonzero_not_silently_swallowed(self, runner):
        with patch("flux.agents.process.AgentProcess", side_effect=RuntimeError("boom")):
            with patch("flux.cli._console_can_render", return_value=True):
                result = runner.invoke(cli, ["agent", "session", "resume", "exec-123"])

        assert result.exit_code != 0
        assert "Error resuming session: boom" in result.output


class TestAgentStop:
    """`flux agent stop` cancels through the workflow-scoped route.

    There is no ``POST /executions/{id}/cancel`` on the server (issue #243) --
    the command used to post to it and 404 on every invocation, while
    reporting the failure with exit code 0.
    """

    def _client(self, execution=None, cancel_status=200):
        client = MagicMock()
        lookup = MagicMock()
        lookup.json.return_value = execution or {
            "execution_id": "exec-1",
            "workflow_namespace": "agents",
            "workflow_name": "agent_chat",
        }
        cancel = MagicMock()
        client.get.side_effect = [lookup, cancel]
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        return client

    def test_stop_cancels_through_the_execution_s_own_workflow(self, runner):
        client = self._client()
        with patch("flux.cli.get_http_client", return_value=client):
            result = runner.invoke(cli, ["agent", "stop", "exec-1"])

        assert result.exit_code == 0, result.output
        urls = [call.args[0] for call in client.get.call_args_list]
        assert urls[0].endswith("/executions/exec-1")
        assert urls[1].endswith("/workflows/agents/agent_chat/cancel/exec-1")
        assert "stopped" in result.output

    def test_custom_workflow_session_cancels_under_its_own_workflow(self, runner):
        """An agent shipping a workflow_file runs as agent_custom_<name>, so
        the workflow cannot be assumed."""
        client = self._client(
            execution={
                "execution_id": "exec-2",
                "workflow_namespace": "agents",
                "workflow_name": "agent_custom_reviewer",
            },
        )
        with patch("flux.cli.get_http_client", return_value=client):
            result = runner.invoke(cli, ["agent", "stop", "exec-2"])

        assert result.exit_code == 0, result.output
        assert (
            client.get.call_args_list[1]
            .args[0]
            .endswith(
                "/workflows/agents/agent_custom_reviewer/cancel/exec-2",
            )
        )

    def test_unknown_session_reports_clearly_and_exits_nonzero(self, runner):
        import httpx

        client = MagicMock()
        response = httpx.Response(404, request=httpx.Request("GET", "http://s/executions/nope"))
        client.get.side_effect = httpx.HTTPStatusError("404", request=None, response=response)
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)

        with patch("flux.cli.get_http_client", return_value=client):
            result = runner.invoke(cli, ["agent", "stop", "nope"])

        assert result.exit_code == 1
        assert "not found" in result.output

    def test_cancel_failure_exits_nonzero(self, runner):
        """A printed error with exit 0 reads as success to a script."""
        client = MagicMock()
        client.get.side_effect = RuntimeError("boom")
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)

        with patch("flux.cli.get_http_client", return_value=client):
            result = runner.invoke(cli, ["agent", "stop", "exec-1"])

        assert result.exit_code == 1
        assert "Error stopping session" in result.output
