"""Tests for the `flux execution approvals/approve/reject` CLI commands."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest
from click.testing import CliRunner

from flux.cli import cli


@pytest.fixture
def runner():
    return CliRunner()


def _mock_client(*, get_json=None, post_json=None, post_status=200):
    mock_client = MagicMock()
    get_resp = MagicMock()
    get_resp.raise_for_status = MagicMock()
    get_resp.json.return_value = get_json or {}

    post_resp = MagicMock()
    post_resp.status_code = post_status
    if post_status >= 400:
        err = httpx.HTTPStatusError("err", request=MagicMock(), response=post_resp)
        post_resp.raise_for_status.side_effect = err
    else:
        post_resp.raise_for_status = MagicMock()
    post_resp.json.return_value = post_json or {}

    mock_client.get.return_value = get_resp
    mock_client.post.return_value = post_resp
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    return mock_client, get_resp, post_resp


class TestExecutionApprovalsList:
    @patch("flux.cli.httpx.Client")
    def test_lists_pending_json(self, mock_class, runner):
        approvals = [
            {
                "approval_id": "appr-abc12345",
                "execution_id": "exec-cli-l-1",
                "task_call_id": "call-cli-1",
                "workflow_namespace": "default",
                "workflow_name": "release",
                "task_name": "deploy",
                "status": "pending",
                "requested_at": "2026-05-07T10:00:00+00:00",
                "decided_at": None,
                "approver": None,
                "reason": None,
            },
        ]
        mock_client, get_resp, _ = _mock_client(
            get_json={"approvals": approvals, "total": 1, "limit": 20, "offset": 0},
        )
        mock_class.return_value = mock_client

        result = runner.invoke(
            cli,
            ["execution", "approvals", "--execution", "exec-cli-l-1", "--json"],
        )

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["approvals"][0]["execution_id"] == "exec-cli-l-1"
        # Verify request shape.
        call_args = mock_client.get.call_args
        assert call_args.args[0].endswith("/approvals")
        params = call_args.kwargs["params"]
        assert params["status"] == "pending"
        assert params["execution_id"] == "exec-cli-l-1"
        assert params["limit"] == 20

    @patch("flux.cli.httpx.Client")
    def test_lists_table_format(self, mock_class, runner):
        approvals = [
            {
                "approval_id": "appr-abc12345",
                "execution_id": "exec-table-1",
                "task_call_id": "call-1",
                "workflow_namespace": "default",
                "workflow_name": "release",
                "task_name": "deploy",
                "status": "pending",
                "requested_at": "2026-05-07T10:00:00+00:00",
                "decided_at": None,
                "approver": None,
                "reason": None,
            },
        ]
        mock_client, _, _ = _mock_client(
            get_json={"approvals": approvals, "total": 1, "limit": 20, "offset": 0},
        )
        mock_class.return_value = mock_client

        result = runner.invoke(cli, ["execution", "approvals"])

        assert result.exit_code == 0, result.output
        assert "appr-abc" in result.output
        assert "default/release/deploy" in result.output
        assert "pending" in result.output

    @patch("flux.cli.httpx.Client")
    def test_workflow_with_namespace_split(self, mock_class, runner):
        mock_client, _, _ = _mock_client(
            get_json={"approvals": [], "total": 0, "limit": 20, "offset": 0},
        )
        mock_class.return_value = mock_client

        result = runner.invoke(
            cli,
            ["execution", "approvals", "--workflow", "ns1/wf1", "--json"],
        )

        assert result.exit_code == 0
        params = mock_client.get.call_args.kwargs["params"]
        assert params["workflow_namespace"] == "ns1"
        assert params["workflow_name"] == "wf1"

    @patch("flux.cli.httpx.Client")
    def test_age_filter_converted_to_iso(self, mock_class, runner):
        mock_client, _, _ = _mock_client(
            get_json={"approvals": [], "total": 0, "limit": 20, "offset": 0},
        )
        mock_class.return_value = mock_client

        result = runner.invoke(
            cli,
            ["execution", "approvals", "--age", "2h", "--json"],
        )

        assert result.exit_code == 0
        params = mock_client.get.call_args.kwargs["params"]
        # 2h => 7200 seconds.
        assert params["age_min"] == "PT7200S"


class TestExecutionApprove:
    @patch("flux.cli.httpx.Client")
    def test_approve_success(self, mock_class, runner):
        mock_client, _, _ = _mock_client(
            post_json={
                "approval_id": "appr-1",
                "status": "approved",
                "execution_state": "RESUMING",
            },
        )
        mock_class.return_value = mock_client

        result = runner.invoke(
            cli,
            ["execution", "approve", "exec-1", "call-a", "--reason", "lgtm"],
        )

        assert result.exit_code == 0, result.output
        assert "Approved" in result.output
        call_args = mock_client.post.call_args
        assert call_args.args[0].endswith("/executions/exec-1/approvals/call-a/approve")
        assert call_args.kwargs["json"] == {"reason": "lgtm"}

    @patch("flux.cli.httpx.Client")
    def test_approve_no_reason(self, mock_class, runner):
        mock_client, _, _ = _mock_client(
            post_json={"status": "approved", "execution_state": "RESUMING"},
        )
        mock_class.return_value = mock_client

        result = runner.invoke(cli, ["execution", "approve", "exec-1", "call-a"])

        assert result.exit_code == 0
        assert mock_client.post.call_args.kwargs["json"] == {}


class TestExecutionReject:
    @patch("flux.cli.httpx.Client")
    def test_reject_success(self, mock_class, runner):
        mock_client, _, _ = _mock_client(
            post_json={
                "status": "rejected",
                "execution_state": "RESUMING",
            },
        )
        mock_class.return_value = mock_client

        result = runner.invoke(
            cli,
            ["execution", "reject", "exec-r", "call-r", "--reason", "no good"],
        )

        assert result.exit_code == 0
        assert "Rejected" in result.output
        assert mock_client.post.call_args.args[0].endswith(
            "/executions/exec-r/approvals/call-r/reject",
        )


class TestExecutionApprove409:
    @patch("flux.cli.httpx.Client")
    def test_409_already_decided_exits_nonzero(self, mock_class, runner):
        mock_client, _, post_resp = _mock_client(
            post_status=409,
            post_json={
                "error": "already_decided",
                "current_status": "approved",
                "decided_at": "2026-05-07T10:00:00+00:00",
            },
        )
        mock_class.return_value = mock_client

        result = runner.invoke(
            cli,
            ["execution", "reject", "exec-409", "call-409"],
        )

        assert result.exit_code != 0
        assert "already_decided" in result.output.lower()


def _mock_two_get_client(execution_json: dict, approvals_json: dict):
    """Mock httpx.Client whose two sequential GETs return execution then approvals."""
    mock_client = MagicMock()
    exec_resp = MagicMock()
    exec_resp.raise_for_status = MagicMock()
    exec_resp.json.return_value = execution_json
    appr_resp = MagicMock()
    appr_resp.raise_for_status = MagicMock()
    appr_resp.json.return_value = approvals_json
    mock_client.get.side_effect = [exec_resp, appr_resp]
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    return mock_client


class TestExecutionShowApprovals:
    @patch("flux.cli.httpx.Client")
    def test_show_lists_pending_approvals(self, mock_class, runner):
        eid = "exec-show-1"
        approvals = {
            "approvals": [
                {
                    "approval_id": "appr-1",
                    "execution_id": eid,
                    "task_call_id": "call-show-1",
                    "workflow_namespace": "default",
                    "workflow_name": "release",
                    "task_name": "deploy",
                    "status": "pending",
                    "requested_at": "2026-05-07T10:00:00+00:00",
                    "decided_at": None,
                    "approver": None,
                    "reason": None,
                },
            ],
            "total": 1,
            "limit": 20,
            "offset": 0,
        }
        mock_class.return_value = _mock_two_get_client(
            {"execution_id": eid, "state": "PAUSED"},
            approvals,
        )

        result = runner.invoke(cli, ["execution", "show", eid])

        assert result.exit_code == 0, result.output
        assert "Pending approvals" in result.output
        assert "call-show-1" in result.output

    @patch("flux.cli.httpx.Client")
    def test_show_omits_section_when_no_approvals(self, mock_class, runner):
        eid = "exec-show-2"
        mock_class.return_value = _mock_two_get_client(
            {"execution_id": eid, "state": "COMPLETED"},
            {"approvals": [], "total": 0, "limit": 20, "offset": 0},
        )

        result = runner.invoke(cli, ["execution", "show", eid])

        assert result.exit_code == 0
        assert "Pending approvals" not in result.output


class TestWorkflowStatusApprovals:
    @patch("flux.cli.httpx.Client")
    def test_status_shows_blocked_count(self, mock_class, runner):
        eid = "exec-stat-1"
        approvals = {
            "approvals": [
                {
                    "approval_id": "appr-1",
                    "execution_id": eid,
                    "task_call_id": "call-stat-1",
                    "workflow_namespace": "default",
                    "workflow_name": "release",
                    "task_name": "deploy",
                    "status": "pending",
                    "requested_at": "2026-05-07T10:00:00+00:00",
                    "decided_at": None,
                    "approver": None,
                    "reason": None,
                },
                {
                    "approval_id": "appr-2",
                    "execution_id": eid,
                    "task_call_id": "call-stat-2",
                    "workflow_namespace": "default",
                    "workflow_name": "release",
                    "task_name": "deploy",
                    "status": "pending",
                    "requested_at": "2026-05-07T10:00:00+00:00",
                    "decided_at": None,
                    "approver": None,
                    "reason": None,
                },
            ],
            "total": 2,
            "limit": 20,
            "offset": 0,
        }
        mock_class.return_value = _mock_two_get_client(
            {"execution_id": eid, "state": "PAUSED"},
            approvals,
        )

        result = runner.invoke(cli, ["workflow", "status", "release", eid])

        assert result.exit_code == 0, result.output
        assert "Blocked on 2 approval" in result.output

    @patch("flux.cli.httpx.Client")
    def test_status_omits_line_when_no_approvals(self, mock_class, runner):
        eid = "exec-stat-2"
        mock_class.return_value = _mock_two_get_client(
            {"execution_id": eid, "state": "COMPLETED"},
            {"approvals": [], "total": 0, "limit": 20, "offset": 0},
        )

        result = runner.invoke(cli, ["workflow", "status", "release", eid])

        assert result.exit_code == 0
        assert "Blocked on" not in result.output
