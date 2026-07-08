"""E2E for the event dispatch plane: sticky routing and health exclusion.

The session-scoped stack in conftest runs the default poll dispatch mode,
where the sticky-routing hint is intentionally ignored. These scenarios need
``[flux.dispatch] mode = "event"``, so this module boots its own server (on a
fresh SQLite database by default; set FLUX_E2E_EVENT_DATABASE_URL to point it
at another scratch backend) plus two labeled workers:

    sticky-a  labels {pin: parent}
    sticky-b  labels {pin: target, starve: true}
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from tests.e2e.conftest import PROJECT_ROOT, FluxCLI, _free_port, _kill_process

pytestmark = pytest.mark.e2e

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def event_cli(tmp_path_factory):
    """Server in event dispatch mode + two labeled workers."""
    tmp = tmp_path_factory.mktemp("e2e-event")
    log_dir = tmp / "logs"
    log_dir.mkdir()
    port = _free_port()
    url = f"http://localhost:{port}"

    env = {
        **os.environ,
        "FLUX_SERVER_PORT": str(port),
        "FLUX_DATABASE_URL": os.environ.get(
            "FLUX_E2E_EVENT_DATABASE_URL",
            f"sqlite:///{tmp / 'flux.db'}",
        ),
        "FLUX_DISPATCH__MODE": "event",
        "FLUX_WORKERS__SERVER_URL": url,
        "FLUX_WORKERS__BOOTSTRAP_TOKEN": "e2e-test-bootstrap-token",
        "FLUX_SECURITY__AUTH__ENABLED": "false",
        "FLUX_SECURITY__AUTH__ALLOW_ANONYMOUS": "true",
        "FLUX_SECURITY__ENCRYPTION__ENCRYPTION_KEY": "e2e-test-encryption-key",
    }

    srv_log = open(log_dir / "server.log", "w")
    srv = subprocess.Popen(
        ["poetry", "run", "flux", "start", "server", "--port", str(port)],
        stdout=srv_log,
        stderr=subprocess.STDOUT,
        cwd=PROJECT_ROOT,
        env=env,
    )

    deadline = time.monotonic() + 60
    healthy = False
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{url}/health", timeout=2).status_code == 200:
                healthy = True
                break
        except httpx.ConnectError:
            pass
        time.sleep(1)
    if not healthy:
        _kill_process(srv, "event-server")
        srv_log.close()
        try:
            tail = (log_dir / "server.log").read_text()[-2000:]
        except OSError:
            tail = "<no server log>"
        pytest.fail(
            f"Event-mode server did not become healthy on port {port} "
            f"within 60s.\n--- server.log tail ---\n{tail}",
        )

    cli = FluxCLI(server_url=url)
    cli._env = env

    # Both workers advertise a 'fitness' metric via the fixture provider —
    # PYTHONPATH makes the tests package importable inside the worker process.
    metrics_env = {
        "FLUX_WORKERS__METRICS_PROVIDER": "tests.e2e.fixtures.worker_metrics:collect",
        "PYTHONPATH": str(PROJECT_ROOT),
    }
    try:
        cli.start_worker(
            "sticky-a",
            labels={"pin": "parent"},
            env={**metrics_env, "FLUX_TEST_FITNESS": "0.2"},
        )
        cli.start_worker(
            "sticky-b",
            labels={"pin": "target", "starve": "true"},
            env={**metrics_env, "FLUX_TEST_FITNESS": "0.9"},
        )
    except RuntimeError:
        for proc in cli._extra_workers:
            _kill_process(proc, "event-worker")
        _kill_process(srv, "event-server")
        srv_log.close()
        raise

    yield cli

    for proc in cli._extra_workers:
        _kill_process(proc, "event-worker")
    _kill_process(srv, "event-server")
    srv_log.close()

    if not os.environ.get("FLUX_E2E_KEEP_LOGS"):
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


def _rows(cli, workflow_name: str) -> list[dict]:
    try:
        listing = cli.execution_list(workflow=workflow_name)
    except ValueError:
        # Zero rows prints "No executions found." instead of JSON.
        return []
    return listing.get("executions", listing if isinstance(listing, list) else [])


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


def test_relayed_child_follows_parent_worker(event_cli):
    """The relayed child lands on the parent's worker every time.

    Least-loaded would pick the OTHER (idle) worker — the parent still
    occupies a slot on its own worker while the relay runs — so the child
    sharing the parent's worker proves the sticky hint won.
    """
    event_cli.register(str(FIXTURES / "sticky_workflow.py"))

    for i in range(3):
        before = {r["execution_id"] for r in _rows(event_cli, "sticky_child")}

        result = event_cli.run("sticky_parent", str(i), mode="sync", timeout=90)

        assert result["state"] == "COMPLETED"
        assert result["output"] == i + 1
        parent_worker = result.get("current_worker")
        assert parent_worker in ("sticky-a", "sticky-b"), parent_worker

        new = [r for r in _rows(event_cli, "sticky_child") if r["execution_id"] not in before]
        assert len(new) == 1, new
        assert new[0].get("worker_name") == parent_worker, new[0]


def test_ineligible_hint_falls_back_to_matching_worker(event_cli):
    """When the hinted worker can't run the child (affinity mismatch), the
    hint is ignored and dispatch falls back to an eligible worker."""
    event_cli.register(str(FIXTURES / "sticky_workflow.py"))

    result = event_cli.run("pinned_parent", "5", mode="sync", timeout=90)

    assert result["state"] == "COMPLETED"
    assert result["output"] == 6
    assert result.get("current_worker") == "sticky-a"

    children = _rows(event_cli, "pinned_child")
    assert children, "pinned_child execution row not found"
    assert all(r.get("worker_name") == "sticky-b" for r in children), children


def test_routing_policy_follows_execution_input(event_cli):
    """Payload-driven locality: prefer("label:pin", "==", input("pin"))
    routes each execution to the worker matching its own input."""
    event_cli.register(str(FIXTURES / "routing_workflow.py"))

    to_parent = event_cli.run("pin_router", '{"pin": "parent"}', mode="sync", timeout=90)
    assert to_parent["state"] == "COMPLETED"
    assert to_parent.get("current_worker") == "sticky-a"

    to_target = event_cli.run("pin_router", '{"pin": "target"}', mode="sync", timeout=90)
    assert to_target["state"] == "COMPLETED"
    assert to_target.get("current_worker") == "sticky-b"


def _wait_for_metric(cli, worker_name: str, metric: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for w in cli.worker_list():
            if w.get("name") == worker_name and metric in (w.get("metrics") or {}):
                return True
        time.sleep(1)
    return False


def test_routing_policy_follows_worker_metric(event_cli):
    """Metric-driven placement: most("metric:fitness") lands every run on
    the worker advertising the highest fitness (sticky-b, 0.9 vs 0.2)."""
    event_cli.register(str(FIXTURES / "routing_workflow.py"))

    # Metrics ride heartbeat pongs — wait until both workers reported.
    assert _wait_for_metric(event_cli, "sticky-a", "fitness", timeout=60)
    assert _wait_for_metric(event_cli, "sticky-b", "fitness", timeout=60)

    for i in range(3):
        result = event_cli.run("fitness_router", str(i), mode="sync", timeout=90)
        assert result["state"] == "COMPLETED"
        assert result.get("current_worker") == "sticky-b", result


def test_event_dispatch_excludes_unhealthy_worker(event_cli):
    """The dispatcher's worker snapshot filters self-reported-unhealthy
    workers, so unconstrained work lands on the healthy one."""
    event_cli.register(str(FIXTURES / "health_workflow.py"))

    blocker = event_cli.run("pinned_loop_blocker", "null", mode="async", timeout=30)
    assert _wait_for_worker_status(event_cli, "sticky-b", "unhealthy", timeout=30)
    assert _worker_status(event_cli, "sticky-a") == "online"

    result = event_cli.run("after_recovery", "null", mode="sync", timeout=60)
    assert result["state"] == "COMPLETED"
    assert result.get("current_worker") == "sticky-a"

    # Leave the stack clean: blocker done, sticky-b healthy again.
    event_cli.wait_for_state(
        "pinned_loop_blocker",
        blocker["execution_id"],
        "COMPLETED",
        timeout=90,
    )
    assert _wait_for_worker_status(event_cli, "sticky-b", "online", timeout=30)
