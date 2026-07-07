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
