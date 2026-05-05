"""E2E tests for worker affinity label-based dispatch."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.e2e


def test_affinity_dispatches_to_matching_worker(cli):
    """Workflow with affinity runs on worker with matching labels."""
    harness = cli.start_worker("harness-worker", labels={"role": "harness"})
    try:
        cli.register(str(FIXTURES / "affinity_workflow.py"))
        r = cli.run("affinity_task", "null")
        assert r["state"] == "COMPLETED"
        assert r.get("current_worker") == "harness-worker"
    finally:
        cli.stop_worker(harness)


def test_affinity_skips_nonmatching_then_dispatches_when_matching_appears(cli):
    """Workflow waits until a worker with matching labels connects."""
    cli.register(str(FIXTURES / "affinity_workflow.py"))

    # Start async — no matching worker yet (e2e-worker has no labels)
    r = cli.run("affinity_task", "null", mode="async", timeout=30)
    exec_id = r["execution_id"]

    # Give the scheduler a moment to attempt dispatch
    time.sleep(3)

    s = cli.status("affinity_task", exec_id)
    if s["state"] == "COMPLETED":
        assert s.get("current_worker") != "e2e-worker"
    else:
        harness = cli.start_worker("harness-worker-2", labels={"role": "harness"})
        try:
            final = cli.wait_for_state("affinity_task", exec_id, "COMPLETED", timeout=30)
            assert final["state"] == "COMPLETED"
        finally:
            cli.stop_worker(harness)


def test_labeled_worker_receives_unconstrained_work(cli):
    """Worker with labels still picks up workflows without affinity."""
    labeled = cli.start_worker("labeled-worker", labels={"role": "harness"})
    try:
        cli.register(str(FIXTURES / "no_affinity_workflow.py"))
        r = cli.run("no_affinity_task", "null")
        assert r["state"] == "COMPLETED"
    finally:
        cli.stop_worker(labeled)


def test_worker_labels_visible_in_list(cli):
    """Worker labels appear in the worker list API response."""
    labeled = cli.start_worker(
        "visible-labels-worker",
        labels={"env": "sandbox", "browser": "true"},
    )
    try:
        workers = cli.worker_list()
        match = [w for w in workers if w["name"] == "visible-labels-worker"]
        assert len(match) == 1
        assert match[0]["labels"] == {"env": "sandbox", "browser": "true"}
    finally:
        cli.stop_worker(labeled)
