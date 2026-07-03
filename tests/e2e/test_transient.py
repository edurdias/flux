"""E2E: transient executions keep the outer lifecycle, drop task-level state.

A transient workflow runs like any other (row, dispatch, terminal state,
visible in execution list) but the worker suppresses every intermediate
checkpoint and the terminal checkpoint carries no TASK_* events.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

FIXTURES = Path(__file__).parent / "fixtures"


def test_transient_run_completes_with_outer_lifecycle_only(cli):
    cli.register(str(FIXTURES / "transient_workflow.py"))

    result = cli.run("transient_double", "5", mode="sync", timeout=60)

    assert result["state"] == "COMPLETED"
    assert result["output"] == 20
    execution_id = result["execution_id"]

    # The execution row exists like any regular run...
    shown = cli.execution_show(execution_id, detailed=True)
    assert shown["state"] == "COMPLETED"

    # ...but no task-level events were persisted: outer lifecycle only.
    types = [e["type"] for e in shown.get("events", [])]
    assert any(t.startswith("WORKFLOW_") for t in types)
    assert not any(t.startswith("TASK_") for t in types), types


def test_transient_pause_becomes_terminal_failure(cli):
    cli.register(str(FIXTURES / "transient_workflow.py"))

    result = cli.run("transient_pause_attempt", '"x"', mode="sync", timeout=60)

    assert result["state"] == "FAILED"
    assert "TransientDurability" in str(result.get("output"))


def test_transient_runs_repeatedly(cli):
    cli.register(str(FIXTURES / "transient_workflow.py"))

    for n in (1, 2, 3):
        result = cli.run("transient_double", str(n), mode="sync", timeout=60)
        assert result["state"] == "COMPLETED"
        assert result["output"] == n * 4
