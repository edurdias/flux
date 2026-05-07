"""E2E tests for the approval-as-task-primitive feature.

Spawns a real server + worker (via the session-scoped ``cli`` fixture in
conftest.py), registers a workflow with ``requires_approval=True`` on its
final task, then exercises the new CLI commands end-to-end:

  flux execution approvals --execution <eid> --json
  flux execution approve   <eid> <call_id>
  flux execution reject    <eid> <call_id>
"""

from __future__ import annotations

import json
from pathlib import Path


FIXTURES = Path(__file__).parent / "fixtures"


def _wait_for_pending_approval(cli, exec_id: str, timeout: int = 30) -> dict:
    """Poll ``execution approvals`` until one PENDING row exists for ``exec_id``."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        out = cli._server_json(
            ["execution", "approvals", "--execution", exec_id, "--json"],
        )
        rows = out.get("approvals", [])
        pending = [r for r in rows if r.get("status") == "pending"]
        if pending:
            return pending[0]
        time.sleep(1)
    raise TimeoutError(f"No pending approval appeared for execution {exec_id} within {timeout}s")


def test_approve_resumes_workflow_to_completion(cli):
    cli.register(str(FIXTURES / "approval_workflow.py"))

    r = cli.run("approval_e2e", '"value-1"', mode="async")
    exec_id = r["execution_id"]

    cli.wait_for_state("approval_e2e", exec_id, "PAUSED", timeout=30)
    pending = _wait_for_pending_approval(cli, exec_id)
    assert pending["task_name"] == "deploy"
    call_id = pending["task_call_id"]

    # --- Approve through the new CLI ---
    proc = cli._server_ok(
        ["execution", "approve", exec_id, call_id, "--reason", "lgtm"],
    )
    assert "Approved" in proc.stdout

    final = cli.wait_for_state("approval_e2e", exec_id, "COMPLETED", timeout=30)
    assert final["state"] == "COMPLETED"

    # The decision should be reflected in the approvals listing.
    out = cli._server_json(
        ["execution", "approvals", "--execution", exec_id, "--status", "all", "--json"],
    )
    rows = out.get("approvals", [])
    assert any(r["task_call_id"] == call_id and r["status"] == "approved" for r in rows), rows


def test_reject_fails_workflow(cli):
    cli.register(str(FIXTURES / "approval_workflow.py"))

    r = cli.run("approval_e2e", '"value-2"', mode="async")
    exec_id = r["execution_id"]

    cli.wait_for_state("approval_e2e", exec_id, "PAUSED", timeout=30)
    pending = _wait_for_pending_approval(cli, exec_id)
    call_id = pending["task_call_id"]

    proc = cli._server_ok(
        ["execution", "reject", exec_id, call_id, "--reason", "no good"],
    )
    assert "Rejected" in proc.stdout

    final = cli.wait_for_state("approval_e2e", exec_id, "FAILED", timeout=30)
    assert final["state"] == "FAILED"

    out = cli._server_json(
        ["execution", "approvals", "--execution", exec_id, "--status", "all", "--json"],
    )
    rows = out.get("approvals", [])
    assert any(r["task_call_id"] == call_id and r["status"] == "rejected" for r in rows), rows


def test_double_decide_returns_409(cli):
    cli.register(str(FIXTURES / "approval_workflow.py"))

    r = cli.run("approval_e2e", '"value-3"', mode="async")
    exec_id = r["execution_id"]

    cli.wait_for_state("approval_e2e", exec_id, "PAUSED", timeout=30)
    pending = _wait_for_pending_approval(cli, exec_id)
    call_id = pending["task_call_id"]

    cli._server_ok(["execution", "approve", exec_id, call_id])

    # The second call must exit non-zero with 'already_decided'.
    proc = cli._server(["execution", "reject", exec_id, call_id])
    assert proc.returncode != 0
    combined = (proc.stdout + proc.stderr).lower()
    assert "already_decided" in combined or "already decided" in combined


def test_workflow_status_blocked_line_on_stderr(cli):
    """Sanity check: the 'Blocked on N approval(s)' line goes to stderr,
    so stdout stays a parseable JSON document for the existing harness."""
    cli.register(str(FIXTURES / "approval_workflow.py"))

    r = cli.run("approval_e2e", '"value-stderr"', mode="async")
    exec_id = r["execution_id"]

    cli.wait_for_state("approval_e2e", exec_id, "PAUSED", timeout=30)
    _wait_for_pending_approval(cli, exec_id)

    proc = cli._server(["workflow", "status", "approval_e2e", exec_id])
    assert proc.returncode == 0
    parsed = json.loads(proc.stdout)
    assert parsed["execution_id"] == exec_id
    assert "Blocked on" in proc.stderr

    # Cleanup: cancel so the test doesn't leave a paused execution behind.
    cli.cancel("approval_e2e", exec_id)
