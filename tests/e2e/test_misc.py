"""E2E tests — miscellaneous: scheduled workflow metadata."""

from __future__ import annotations


def test_scheduled_workflow_inline(cli):
    """Register a workflow that declares a schedule via decorator metadata."""
    cli.register("examples/scheduling/simple_backup.py")
    wf = cli.show("database_backup")
    assert wf["name"] == "database_backup"
