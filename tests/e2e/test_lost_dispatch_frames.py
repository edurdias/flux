"""E2E: an execution whose dispatch frame never reached its worker recovers.

A frame lost in transit leaves the row SCHEDULED to a worker that never
claims it. The test process plays the lost frame: it assigns a CREATED row to
a worker through the dispatcher's own query (generation bumped, schedule event
written) and sends nothing. Before the fix the row stayed SCHEDULED for as
long as the worker kept its connection; each test boots its own server so
the claim deadline can be on or off.
"""

from __future__ import annotations

import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from tests.e2e.conftest import PROJECT_ROOT, FluxCLI, _free_port, _kill_process

pytestmark = pytest.mark.e2e

FIXTURES = Path(__file__).parent / "fixtures"
WORKFLOW = "lost_frame_task"


@contextmanager
def _server(tmp: Path, *, mode: str, claim_timeout: int):
    port = _free_port()
    url = f"http://localhost:{port}"
    db_url = f"sqlite:///{tmp / 'flux.db'}"
    env = {
        **os.environ,
        "FLUX_SERVER_PORT": str(port),
        "FLUX_DATABASE_URL": db_url,
        "FLUX_DISPATCH__MODE": mode,
        "FLUX_DISPATCH__FALLBACK_INTERVAL": "1",
        "FLUX_SCHEDULING__POLL_INTERVAL": "1",
        "FLUX_WORKERS__CLAIM_TIMEOUT": str(claim_timeout),
        "FLUX_WORKERS__SERVER_URL": url,
        "FLUX_WORKERS__BOOTSTRAP_TOKEN": "e2e-test-bootstrap-token",
        "FLUX_SECURITY__AUTH__ENABLED": "false",
        "FLUX_SECURITY__AUTH__ALLOW_ANONYMOUS": "true",
        "FLUX_SECURITY__ENCRYPTION__ENCRYPTION_KEY": "e2e-test-encryption-key",
    }
    log = open(tmp / "server.log", "w")
    srv = subprocess.Popen(
        ["poetry", "run", "flux", "start", "server", "--port", str(port)],
        stdout=log,
        stderr=subprocess.STDOUT,
        cwd=PROJECT_ROOT,
        env=env,
    )
    cli = FluxCLI(server_url=url)
    cli._env = env
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"{url}/health", timeout=2).status_code == 200:
                    break
            except httpx.ConnectError:
                pass
            time.sleep(1)
        else:
            pytest.fail(f"server did not become healthy:\n{(tmp / 'server.log').read_text()}")
        yield cli, db_url
    finally:
        for proc in list(cli._extra_workers):
            _kill_process(proc, "worker")
        _kill_process(srv, "server")
        log.close()


def _lose_a_frame(db_url: str, execution_id: str, worker_name: str) -> None:
    """Assign the row to the worker exactly as dispatch does, minus the frame."""
    from flux.config import Configuration
    from flux.context_managers import DatabaseContextManager
    from flux.models import DatabaseRepository
    from flux.worker_registry import DatabaseWorkerRegistry

    Configuration.get().override(database_url=db_url)
    DatabaseRepository._engines.clear()
    try:
        worker = DatabaseWorkerRegistry().get(worker_name)
        assigned = DatabaseContextManager().next_executions_batch([worker], limit=10)
        assert [ctx.execution_id for ctx, _ in assigned] == [execution_id]
    finally:
        DatabaseRepository._engines.clear()


def _wait_for_state(cli: FluxCLI, execution_id: str, state: str, timeout: float) -> str | None:
    deadline = time.monotonic() + timeout
    seen = None
    while time.monotonic() < deadline:
        seen = cli.execution_show(execution_id).get("state")
        if seen == state:
            return seen
        time.sleep(0.5)
    return seen


def _submit_parked(cli: FluxCLI, worker_name: str) -> str:
    """Register the worker, then take it offline so the execution stays CREATED."""
    worker = cli.start_worker(worker_name)
    cli.register(str(FIXTURES / "lost_frame_workflow.py"))
    cli.stop_worker(worker)
    execution_id = cli.run(WORKFLOW, mode="async")["execution_id"]
    assert cli.execution_show(execution_id)["state"] == "CREATED"
    return execution_id


@pytest.mark.parametrize("mode", ["poll", "event"])
def test_reconnect_releases_an_assignment_whose_frame_was_lost(tmp_path, mode):
    with _server(tmp_path, mode=mode, claim_timeout=0) as (cli, db_url):
        execution_id = _submit_parked(cli, "lost-frame-w")
        _lose_a_frame(db_url, execution_id, "lost-frame-w")
        assert cli.execution_show(execution_id)["state"] == "SCHEDULED"

        cli.start_worker("lost-frame-w")

        assert _wait_for_state(cli, execution_id, "COMPLETED", timeout=30) == "COMPLETED"


def test_claim_deadline_releases_an_assignment_nobody_claims(tmp_path):
    """The row is lost to a worker that never comes back; another worker
    takes it once the deadline passes, well before the ~60 s orphan reclaim
    for a stale worker would."""
    with _server(tmp_path, mode="event", claim_timeout=2) as (cli, db_url):
        execution_id = _submit_parked(cli, "lost-frame-gone")
        _lose_a_frame(db_url, execution_id, "lost-frame-gone")

        cli.start_worker("lost-frame-other")

        assert _wait_for_state(cli, execution_id, "COMPLETED", timeout=25) == "COMPLETED"
