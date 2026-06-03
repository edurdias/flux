"""Distributed cross-process replay guard.

A workflow runs on a worker, pauses, then the worker is restarted as a new
process with a different PYTHONHASHSEED (modelling a redeploy/crash-restart
while the execution is paused) and resumes the execution. Pre-pause tasks must
replay, not re-execute. Exercises the full server+worker path (SSE claim, HTTP
checkpoint round-trip, sticky resume routing) on top of the deterministic
task_id fix: under the old per-process hash() task ids, the restarted worker
recomputed different ids, missed the replay short-circuit, and re-ran every
pre-pause task.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

FIXTURES = Path(__file__).parent / "fixtures"
WORKER = "handoff-worker"


def _lines(p: Path) -> list[str]:
    return [ln for ln in p.read_text().splitlines() if ln]


def _is_online(cli) -> bool:
    return any(w.get("name") == WORKER and w.get("status") == "online" for w in cli.worker_list())


def _wait(cli, online: bool, timeout: int = 60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _is_online(cli) is online:
            return
        time.sleep(1)
    raise TimeoutError(f"worker {WORKER} did not become {'online' if online else 'offline'}")


def test_completed_tasks_replay_on_worker_restart(cli):
    counter = Path(tempfile.mkdtemp(prefix="flux-handoff-")) / "exec.log"
    counter.write_text("")
    cli.register(str(FIXTURES / "handoff_workflow.py"))

    orig_seed = cli._env.get("PYTHONHASHSEED")
    worker = None
    try:
        # Run on the worker (seed 0): records a + b, then pauses.
        cli._env["PYTHONHASHSEED"] = "0"
        worker = cli.start_worker(WORKER, labels={"role": "handoff"})
        _wait(cli, online=True)

        r = cli.run("handoff_workflow", json.dumps(str(counter)), mode="async", timeout=30)
        exec_id = r["execution_id"]
        s = cli.wait_for_state("handoff_workflow", exec_id, "PAUSED", timeout=60)
        assert s.get("current_worker") == WORKER
        assert _lines(counter) == ["a", "b"]

        # Restart the same worker name in a new process with a different hash
        # seed; sequence the disconnect before the reconnect to avoid a
        # same-name registration race.
        cli.stop_worker(worker)
        worker = None
        _wait(cli, online=False, timeout=30)
        cli._env["PYTHONHASHSEED"] = "1"
        worker = cli.start_worker(WORKER, labels={"role": "handoff"})
        _wait(cli, online=True)

        cli.resume("handoff_workflow", exec_id)
        final = cli.wait_for_state("handoff_workflow", exec_id, "COMPLETED", timeout=60)
        assert final.get("current_worker") == WORKER

        # a + b ran once before the restart; only c ran after — no re-execution.
        assert _lines(counter) == ["a", "b", "c"], (
            f"expected no re-execution across worker restart, got {_lines(counter)}"
        )
    finally:
        if worker is not None:
            cli.stop_worker(worker)
        if orig_seed is None:
            cli._env.pop("PYTHONHASHSEED", None)
        else:
            cli._env["PYTHONHASHSEED"] = orig_seed
