"""E2E: pluggable runners.

The worker default is the subprocess runner, so the whole e2e suite already
exercises child-process execution; this file covers the runner-specific
scenarios: per-workflow pinning, hard crashes mapped to durability (durable
release + replay-resume, transient terminal failure).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

FIXTURES = Path(__file__).parent / "fixtures"


def test_inprocess_pinned_workflow_completes(cli):
    cli.register(str(FIXTURES / "runner_workflow.py"))

    result = cli.run("inprocess_pinned", "41", mode="sync", timeout=60)

    assert result["state"] == "COMPLETED"
    assert result["output"] == 42


def test_subprocess_pinned_workflow_completes(cli):
    cli.register(str(FIXTURES / "runner_workflow.py"))

    result = cli.run("subprocess_pinned", "41", mode="sync", timeout=60)

    assert result["state"] == "COMPLETED"
    assert result["output"] == 42


def test_durable_crash_releases_and_resumes_from_replay(cli):
    """A hard child crash must not strand or duplicate a durable execution.

    First dispatch: record_completed checkpoints, attempt_and_maybe_crash
    kills the process. The worker releases the claim; the server re-dispatches;
    replay short-circuits record_completed (exactly one 'ran' line) while the
    never-completed crash task re-runs (two 'attempt' lines) and succeeds.
    """
    cli.register(str(FIXTURES / "runner_workflow.py"))
    base_dir = tempfile.mkdtemp(prefix="flux-runner-crash-")

    r = cli.run("durable_crash_once", f'"{base_dir}"', mode="async", timeout=30)
    final = cli.wait_for_state(
        "durable_crash_once",
        r["execution_id"],
        "COMPLETED",
        timeout=120,
    )

    assert final["state"] == "COMPLETED"
    assert final["output"] == "survived"
    attempts = (Path(base_dir) / "attempts").read_text().splitlines()
    completed = (Path(base_dir) / "completed").read_text().splitlines()
    assert len(attempts) == 2, attempts  # crashed once, survived once
    assert len(completed) == 1, completed  # replay short-circuited the done task


def test_transient_crash_fails_terminally(cli):
    """Transient executions are at-most-once: a crash is a terminal FAILED,
    never a silent re-dispatch."""
    cli.register(str(FIXTURES / "runner_workflow.py"))

    result = cli.run("transient_crash", '"x"', mode="sync", timeout=90)

    assert result["state"] == "FAILED"
    assert "WorkerProcessCrashed" in str(result.get("output"))
