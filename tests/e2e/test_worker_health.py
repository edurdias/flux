"""E2E: a worker that starves its own event loop steps out of dispatch.

The loop_blocker workflow (pinned to the inprocess runner) blocks the
worker's loop in bursts; the health probe accumulates breaches, the worker
reports unhealthy on its pong, the server surfaces it in GET /workers, and
once the blocker finishes the worker recovers and runs work again.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

FIXTURES = Path(__file__).parent / "fixtures"


def _worker_statuses(cli) -> set[str]:
    return {w.get("status") for w in cli.worker_list()}


def _wait_for_status(cli, status: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if status in _worker_statuses(cli):
            return True
        time.sleep(1)
    return False


def _worker_status(cli, name: str) -> str | None:
    for w in cli.worker_list():
        if w.get("name") == name:
            return w.get("status")
    return None


def _wait_for_worker_status(cli, name: str, status: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _worker_status(cli, name) == status:
            return True
        time.sleep(1)
    return False


def test_starved_worker_reports_unhealthy_then_recovers(cli):
    cli.register(str(FIXTURES / "health_workflow.py"))

    r = cli.run("loop_blocker", "null", mode="async", timeout=30)

    # Detection: bursts of blocking trip the probe; the pong propagates the
    # state and GET /workers surfaces it.
    assert _wait_for_status(cli, "unhealthy", timeout=30), _worker_statuses(cli)

    # The blocker ends; three clean probes recover the worker.
    final = cli.wait_for_state("loop_blocker", r["execution_id"], "COMPLETED", timeout=60)
    assert final["state"] == "COMPLETED"
    assert _wait_for_status(cli, "online", timeout=30), _worker_statuses(cli)

    # And it accepts + completes new work again.
    result = cli.run("after_recovery", "null", mode="sync", timeout=60)
    assert result["state"] == "COMPLETED"
    assert result["output"] == "healthy again"


def test_work_submitted_while_unhealthy_completes_after_recovery(cli):
    """Work queued during the unhealthy window is held back (the poll loop
    skips the worker) and dispatched once the worker recovers."""
    cli.register(str(FIXTURES / "health_workflow.py"))

    blocker = cli.run("loop_blocker", "null", mode="async", timeout=30)
    assert _wait_for_status(cli, "unhealthy", timeout=30), _worker_statuses(cli)

    queued = cli.run("after_recovery", "null", mode="async", timeout=30)

    # While the worker still reports unhealthy the new execution must not be
    # picked up. Sample worker status around each state read so a recovery
    # mid-loop can't produce a false failure.
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        before = _worker_statuses(cli)
        state = cli.status("after_recovery", queued["execution_id"])["state"]
        after = _worker_statuses(cli)
        if "unhealthy" in before and "unhealthy" in after:
            assert state in ("CREATED", "SCHEDULED"), state
        if "unhealthy" not in after:
            break

    # Recovery: the blocker ends, the worker heals, the queued work runs.
    cli.wait_for_state("loop_blocker", blocker["execution_id"], "COMPLETED", timeout=90)
    final = cli.wait_for_state("after_recovery", queued["execution_id"], "COMPLETED", timeout=60)
    assert final["state"] == "COMPLETED"
    assert _wait_for_status(cli, "online", timeout=30), _worker_statuses(cli)


def test_unpinned_work_routes_around_unhealthy_worker(cli):
    """With a second worker starved (blocker pinned by label), unconstrained
    work keeps flowing to the healthy worker."""
    starved = cli.start_worker("starved-worker", labels={"starve": "true"})
    try:
        cli.register(str(FIXTURES / "health_workflow.py"))

        blocker = cli.run("pinned_loop_blocker", "null", mode="async", timeout=30)
        assert _wait_for_worker_status(cli, "starved-worker", "unhealthy", timeout=30)
        assert _worker_status(cli, "e2e-worker") == "online"

        # Unconstrained work completes on the healthy worker while the
        # starved one is excluded from dispatch.
        result = cli.run("after_recovery", "null", mode="sync", timeout=60)
        assert result["state"] == "COMPLETED"
        assert result.get("current_worker") != "starved-worker"

        # The starved worker finishes its blocker and recovers.
        cli.wait_for_state(
            "pinned_loop_blocker",
            blocker["execution_id"],
            "COMPLETED",
            timeout=90,
        )
        assert _wait_for_worker_status(cli, "starved-worker", "online", timeout=30)
    finally:
        cli.stop_worker(starved)
