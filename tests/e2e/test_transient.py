"""E2E: transient executions keep the outer lifecycle, drop task-level state.

A transient workflow runs like any other (row, dispatch, terminal state,
visible in execution list) but the worker suppresses every intermediate
checkpoint and the terminal checkpoint carries no TASK_* events. These tests
cover the full scenario matrix: sync/async completion, failure, in-memory
retry, cancellation, the durability guards (pause, approval), the durable
regression control, and a durable->transient mesh hop via call().
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

FIXTURES = Path(__file__).parent / "fixtures"


def _event_types(cli, exec_id: str) -> list[str]:
    shown = cli.execution_show(exec_id, detailed=True)
    return [e["type"] for e in shown.get("events", [])]


def _assert_outer_lifecycle_only(cli, exec_id: str):
    types = _event_types(cli, exec_id)
    assert any(t.startswith("WORKFLOW_") for t in types), types
    assert not any(t.startswith("TASK_") for t in types), types


def test_transient_sync_completes_with_outer_lifecycle_only(cli):
    cli.register(str(FIXTURES / "transient_workflow.py"))

    result = cli.run("transient_double", "5", mode="sync", timeout=60)

    assert result["state"] == "COMPLETED"
    assert result["output"] == 20
    shown = cli.execution_show(result["execution_id"], detailed=True)
    assert shown["state"] == "COMPLETED"
    _assert_outer_lifecycle_only(cli, result["execution_id"])


def test_transient_async_mode(cli):
    cli.register(str(FIXTURES / "transient_workflow.py"))

    r = cli.run("transient_double", "7", mode="async", timeout=30)
    final = cli.wait_for_state("transient_double", r["execution_id"], "COMPLETED", timeout=60)

    assert final["state"] == "COMPLETED"
    _assert_outer_lifecycle_only(cli, r["execution_id"])


def test_transient_failure_persists_terminal_state(cli):
    cli.register(str(FIXTURES / "transient_workflow.py"))

    result = cli.run("transient_failing", '"x"', mode="sync", timeout=60)

    assert result["state"] == "FAILED"
    assert "boom" in str(result.get("output"))
    _assert_outer_lifecycle_only(cli, result["execution_id"])


def test_transient_task_retry_works_in_memory(cli):
    """Task retry/fallback semantics run in-memory: a task failing its first
    attempt recovers on retry, and none of it is persisted."""
    cli.register(str(FIXTURES / "transient_workflow.py"))
    marker = Path(tempfile.mkdtemp(prefix="flux-transient-")) / "attempt"

    result = cli.run("transient_retry", f'"{marker}"', mode="sync", timeout=60)

    assert result["state"] == "COMPLETED"
    assert result["output"] == "recovered"
    assert marker.exists()  # first attempt really ran and failed
    _assert_outer_lifecycle_only(cli, result["execution_id"])


def test_transient_pause_becomes_terminal_failure(cli):
    cli.register(str(FIXTURES / "transient_workflow.py"))

    result = cli.run("transient_pause_attempt", '"x"', mode="sync", timeout=60)

    assert result["state"] == "FAILED"
    assert "TransientDurability" in str(result.get("output"))


def test_transient_approval_gate_is_a_hard_error(cli):
    cli.register(str(FIXTURES / "transient_workflow.py"))

    result = cli.run("transient_approval_attempt", "1", mode="sync", timeout=60)

    assert result["state"] == "FAILED"
    output = str(result.get("output"))
    assert "TransientDurability" in output or "requires_approval" in output


def test_transient_cancellation(cli):
    cli.register(str(FIXTURES / "transient_workflow.py"))

    r = cli.run("transient_slow", '"x"', mode="async", timeout=30)
    exec_id = r["execution_id"]
    cli.wait_for_state("transient_slow", exec_id, "RUNNING", timeout=60)
    # Give the workflow a beat to be genuinely executing on the worker.
    time.sleep(2)

    cli.cancel("transient_slow", exec_id)
    final = cli.wait_for_state("transient_slow", exec_id, "CANCELLED", timeout=60)

    assert final["state"] == "CANCELLED"
    _assert_outer_lifecycle_only(cli, exec_id)


def test_durable_sibling_still_persists_task_events(cli):
    """Regression control: durability suppression must not leak to durable
    workflows registered from the same source file."""
    cli.register(str(FIXTURES / "transient_workflow.py"))

    result = cli.run("durable_sibling", "5", mode="sync", timeout=60)

    assert result["state"] == "COMPLETED"
    types = _event_types(cli, result["execution_id"])
    assert any(t.startswith("TASK_") for t in types), types


def test_durable_parent_calls_transient_child(cli):
    """Mesh hop: a durable workflow invokes a transient one via call()."""
    cli.register(str(FIXTURES / "transient_workflow.py"))

    result = cli.run("durable_calls_transient", "3", mode="sync", timeout=90)

    assert result["state"] == "COMPLETED"
    assert result["output"] == 12


def _execution_count(cli, workflow_name: str) -> int:
    try:
        listing = cli.execution_list(workflow=workflow_name)
    except Exception:
        # Zero rows prints "No executions found." instead of JSON.
        return 0
    executions = listing.get("executions", listing if isinstance(listing, list) else [])
    return len(executions)


def test_transient_fast_path_skips_dispatch_entirely(cli):
    """A transient child called by *object* runs in-process on the worker:
    correct result, and no child execution row is ever created."""
    cli.register(str(FIXTURES / "transient_workflow.py"))
    child_rows_before = _execution_count(cli, "transient_double")

    result = cli.run("durable_calls_transient_fast", "3", mode="sync", timeout=90)

    assert result["state"] == "COMPLETED"
    assert result["output"] == 12
    assert _execution_count(cli, "transient_double") == child_rows_before
