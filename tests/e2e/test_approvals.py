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
    pending = _wait_for_pending_approval(cli, exec_id)

    proc = cli._server(["workflow", "status", "approval_e2e", exec_id])
    assert proc.returncode == 0
    parsed = json.loads(proc.stdout)
    assert parsed["execution_id"] == exec_id
    assert "Blocked on" in proc.stderr

    # Drive the workflow to a terminal state so it doesn't leave a paused
    # execution behind that subsequent tests inherit through the shared
    # session-scoped server. Approve and wait for COMPLETED before returning.
    cli._server_ok(["execution", "approve", exec_id, pending["task_call_id"]])
    cli.wait_for_state("approval_e2e", exec_id, "COMPLETED", timeout=30)


def test_approve_always_covers_later_gates_on_same_task(cli):
    """#74 e2e: `flux execution approve --always` is a standing grant — later
    approval gates on the same task within the execution auto-approve without
    pausing, each leaving a materialized audit row."""
    cli.register(str(FIXTURES / "approval_workflow.py"))

    r = cli.run("approval_multi_e2e", '["one", "two", "three"]', mode="async")
    exec_id = r["execution_id"]

    # First `deploy` call pauses at its gate; grant it for the whole execution.
    cli.wait_for_state("approval_multi_e2e", exec_id, "PAUSED", timeout=30)
    pending = _wait_for_pending_approval(cli, exec_id)
    assert pending["task_name"] == "deploy"

    proc = cli._server_ok(
        ["execution", "approve", exec_id, pending["task_call_id"], "--always"],
    )
    assert "Approved" in proc.stdout

    # The remaining two `deploy` calls must ride the grant: one resume drives
    # the workflow to completion with no further pending approvals.
    final = cli.wait_for_state("approval_multi_e2e", exec_id, "COMPLETED", timeout=60)
    assert final["state"] == "COMPLETED"
    assert final.get("output") == ["deployed:one", "deployed:two", "deployed:three"]

    out = cli._server_json(
        ["execution", "approvals", "--execution", exec_id, "--status", "all", "--json"],
    )
    rows = out.get("approvals", [])
    assert all(row["status"] == "approved" for row in rows), rows
    assert len(rows) == 3, rows
    # One operator decision carrying the execution scope...
    grants = [row for row in rows if row["scope"] == "execution"]
    assert len(grants) == 1, rows
    assert grants[0]["task_call_id"] == pending["task_call_id"]
    # ...and one materialized audit row per auto-approved later gate.
    materialized = [row for row in rows if row["reason"] == "standing grant"]
    assert len(materialized) == 2, rows
    assert all(row["scope"] == "call" for row in materialized), rows


def test_retry_approval_resumes_into_the_retry_attempt(cli):
    """#72 e2e: approving a retry-attempt gate resumes INTO that attempt —
    the original attempt's side effects are not duplicated by the replay,
    across real worker processes."""
    import tempfile
    import time
    from pathlib import Path

    cli.register(str(FIXTURES / "approval_workflow.py"))
    marker = Path(tempfile.mkdtemp(prefix="flux-retry-approval-")) / "attempts"

    r = cli.run("approval_retry_e2e", f'"{marker}"', mode="async")
    exec_id = r["execution_id"]

    # Original call's gate pauses the workflow; approve it.
    cli.wait_for_state("approval_retry_e2e", exec_id, "PAUSED", timeout=30)
    pending = _wait_for_pending_approval(cli, exec_id)
    cli._server_ok(["execution", "approve", exec_id, pending["task_call_id"]])

    # The body runs once (fails), then the retry attempt pauses at its own
    # gate — poll for the retry-scoped pending approval.
    deadline = time.monotonic() + 30
    retry_pending = None
    while time.monotonic() < deadline:
        out = cli._server_json(
            ["execution", "approvals", "--execution", exec_id, "--json"],
        )
        rows = [
            row
            for row in out.get("approvals", [])
            if row.get("status") == "pending" and row["task_call_id"].endswith("~retry1")
        ]
        if rows:
            retry_pending = rows[0]
            break
        time.sleep(1)
    assert retry_pending is not None, "retry-attempt approval never appeared"
    assert marker.read_text() == "1"

    cli._server_ok(["execution", "approve", exec_id, retry_pending["task_call_id"]])

    final = cli.wait_for_state("approval_retry_e2e", exec_id, "COMPLETED", timeout=60)
    assert final["state"] == "COMPLETED"
    assert final.get("output") == "deployed-after-2-attempts"
    # Exactly two body runs: original + the approved retry. Before the #72
    # fix the resume replayed the original attempt again (three runs).
    assert marker.read_text() == "2"
