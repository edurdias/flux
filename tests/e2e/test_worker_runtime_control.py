"""E2E: worker runtime pause/resume, bulk cancel, and heartbeat status.

'flux worker pause|resume|cancel-all|status <name>' speak the worker's
local control socket (the worker is outbound-only). Paused reads as
*paused, not offline*: heartbeats keep flowing, the server surfaces the
state in GET /workers and stops dispatching; pinned work waits and runs
after resume. cancel-all is the worker-initiated bulk cancel — in-flight
executions reach a terminal CANCELLED through their normal path.
SIGUSR1/SIGUSR2 are the signal equivalents of pause/resume.
"""

from __future__ import annotations

import signal
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

FIXTURES = Path(__file__).parent / "fixtures"


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


def test_pause_holds_pinned_work_until_resume(cli):
    worker = cli.start_worker("pausable-worker", labels={"pausable": "true"})
    try:
        cli.register(str(FIXTURES / "worker_control_workflow.py"))

        # Local status before anything: active, nothing in flight.
        status = cli.worker_local_status("pausable-worker")
        assert status["status"] == "active"
        assert status["in_flight"] == 0

        status = cli.worker_pause("pausable-worker")
        assert status["status"] == "paused"

        # The pause pong propagates immediately; the server surfaces the
        # deliberate state (not offline, not unhealthy).
        assert _wait_for_worker_status(cli, "pausable-worker", "paused", timeout=30)

        # Work pinned to the paused worker must not start.
        r = cli.run("pause_pinned_quick", "null", mode="async", timeout=30)
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            state = cli.status("pause_pinned_quick", r["execution_id"])["state"]
            assert state in ("CREATED", "SCHEDULED"), state
            time.sleep(1)

        status = cli.worker_resume("pausable-worker")
        assert status["status"] == "active"
        assert _wait_for_worker_status(cli, "pausable-worker", "online", timeout=30)

        # The held execution dispatches and completes after resume.
        final = cli.wait_for_state(
            "pause_pinned_quick",
            r["execution_id"],
            "COMPLETED",
            timeout=60,
        )
        assert final["state"] == "COMPLETED"
        assert final["output"] == "ran after resume"
    finally:
        cli.stop_worker(worker)


def test_cancel_all_cancels_inflight_work(cli):
    worker = cli.start_worker("yieldable-worker", labels={"pausable": "true"})
    try:
        cli.register(str(FIXTURES / "worker_control_workflow.py"))

        r = cli.run("pause_pinned_slow", "120", mode="async", timeout=30)
        cli.wait_for_state("pause_pinned_slow", r["execution_id"], "RUNNING", timeout=60)

        # The worker sees its own in-flight work on the local status.
        status = cli.worker_local_status("yieldable-worker")
        assert status["in_flight"] >= 1

        response = cli.worker_cancel_all("yieldable-worker")
        assert response["cancelled"] >= 1

        # The execution reaches a terminal cancel through its normal path
        # (runner term signal -> workflow cancellation -> checkpoint).
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            s = cli.status("pause_pinned_slow", r["execution_id"])
            if s["state"] in ("CANCELLED", "CANCELLING"):
                break
            time.sleep(2)
        assert s["state"] in ("CANCELLED", "CANCELLING"), s["state"]

        # The worker is free again (busy-yield: resources released in
        # seconds without the worker going offline).
        status = cli.worker_local_status("yieldable-worker")
        assert status["in_flight"] == 0
        assert _worker_status(cli, "yieldable-worker") in ("online", "paused")
    finally:
        cli.stop_worker(worker)


def test_sigusr_signals_pause_and_resume(cli):
    worker = cli.start_worker("signal-worker", labels={"pausable": "true"})
    try:
        worker.send_signal(signal.SIGUSR1)
        assert _wait_for_worker_status(cli, "signal-worker", "paused", timeout=30)

        worker.send_signal(signal.SIGUSR2)
        assert _wait_for_worker_status(cli, "signal-worker", "online", timeout=30)
    finally:
        cli.stop_worker(worker)


def test_paused_status_filter_in_worker_list(cli):
    worker = cli.start_worker("filterable-worker", labels={"pausable": "true"})
    try:
        cli.worker_pause("filterable-worker")
        assert _wait_for_worker_status(cli, "filterable-worker", "paused", timeout=30)

        paused_names = {w["name"] for w in cli.worker_list() if w["status"] == "paused"}
        assert "filterable-worker" in paused_names
        # The session worker stays dispatchable and unaffected.
        assert _worker_status(cli, "e2e-worker") == "online"

        cli.worker_resume("filterable-worker")
        assert _wait_for_worker_status(cli, "filterable-worker", "online", timeout=30)
    finally:
        cli.stop_worker(worker)
