"""Acknowledged events survive an abrupt server death (#262).

The engine's durability claim is narrow and worth pinning: once the server
has answered a checkpoint, those events are committed, and a SIGKILL --
no graceful shutdown, no flush, no chance to write anything on the way
out -- does not take them with it.

What this does *not* prove, and the docs say so: SQLite runs
`synchronous=NORMAL` under WAL by default, which trades the fsync on every
commit for throughput. That is safe against a *process* dying (the OS
still owns the written pages) but not against the machine losing power or
the kernel panicking, where the last commits can be lost. Testing the
latter needs a real power cut, not a signal.

Spawns its own server and worker rather than using the session `cli`
fixture: this test kills the server, which every other test in the
session still needs.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

WORKFLOW = """
from flux import ExecutionContext, task, workflow


@task
async def durable_step(index: int) -> int:
    return index


@workflow
async def durability_probe(ctx: ExecutionContext):
    total = 0
    for index in range(12):
        total += await durable_step(index)
    return total
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_healthy(url: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{url}/health", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"server never became healthy on {url}")


class _Fleet:
    """A server and worker of this test's own, on a throwaway database."""

    def __init__(self, tmp_path: Path):
        self.port = _free_port()
        self.url = f"http://localhost:{self.port}"
        self.db_path = tmp_path / "durability.db"
        self.log_dir = tmp_path / "logs"
        self.log_dir.mkdir(exist_ok=True)
        self.env = {
            **os.environ,
            "FLUX_SERVER_PORT": str(self.port),
            "FLUX_DATABASE_URL": f"sqlite:///{self.db_path}",
            "FLUX_WORKERS__SERVER_URL": self.url,
            "FLUX_WORKERS__BOOTSTRAP_TOKEN": "durability-test-token",
            "FLUX_SECURITY__AUTH__ENABLED": "false",
            "FLUX_SECURITY__AUTH__ALLOW_ANONYMOUS": "true",
            "FLUX_SECURITY__ENCRYPTION__ENCRYPTION_KEY": "durability-test-key",
        }
        self.server: subprocess.Popen | None = None
        self.worker: subprocess.Popen | None = None
        self._logs: list = []

    def start_server(self, name: str = "server") -> None:
        log = open(self.log_dir / f"{name}.log", "w")
        self._logs.append(log)
        self.server = subprocess.Popen(
            ["poetry", "run", "flux", "start", "server", "--port", str(self.port)],
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=PROJECT_ROOT,
            env=self.env,
        )
        _wait_healthy(self.url)

    def start_worker(self) -> None:
        log = open(self.log_dir / "worker.log", "w")
        self._logs.append(log)
        self.worker = subprocess.Popen(
            [
                "poetry",
                "run",
                "flux",
                "start",
                "worker",
                "durability-worker",
                "--server-url",
                self.url,
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=PROJECT_ROOT,
            env=self.env,
        )

    def kill_server(self) -> None:
        """SIGKILL: no shutdown hook, no flush, nothing written on the way out."""
        assert self.server is not None
        self.server.send_signal(signal.SIGKILL)
        self.server.wait(timeout=30)

    def stop(self) -> None:
        for proc in (self.worker, self.server):
            if proc is None or proc.poll() is not None:
                continue
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
        for log in self._logs:
            log.close()


@pytest.fixture
def fleet(tmp_path):
    fleet = _Fleet(tmp_path)
    fleet.start_server()
    fleet.start_worker()
    yield fleet
    fleet.stop()


def _events(url: str, namespace: str, name: str, execution_id: str) -> list[tuple[str, str]]:
    response = httpx.get(
        f"{url}/workflows/{namespace}/{name}/status/{execution_id}",
        params={"detailed": True},
        timeout=30,
    )
    response.raise_for_status()
    body = response.json()
    return [(event["type"], event["source_id"]) for event in body.get("events", [])]


def test_acknowledged_events_survive_a_server_sigkill(fleet, tmp_path):
    source = tmp_path / "durability_probe.py"
    source.write_text(WORKFLOW)
    with open(source, "rb") as handle:
        registered = httpx.post(
            f"{fleet.url}/workflows",
            files={"file": (source.name, handle, "text/x-python")},
            timeout=60,
        )
    registered.raise_for_status()

    started = httpx.post(
        f"{fleet.url}/workflows/default/durability_probe/run/async",
        json=None,
        timeout=60,
    )
    started.raise_for_status()
    execution_id = started.json()["execution_id"]

    deadline = time.monotonic() + 180
    state = None
    while time.monotonic() < deadline:
        status = httpx.get(
            f"{fleet.url}/workflows/default/durability_probe/status/{execution_id}",
            timeout=30,
        ).json()
        state = status.get("state")
        if state in ("COMPLETED", "FAILED", "CANCELLED"):
            break
        time.sleep(0.25)
    assert state == "COMPLETED", f"probe execution ended {state}"

    # Everything the server has answered for, before it dies.
    acknowledged = _events(fleet.url, "default", "durability_probe", execution_id)
    assert acknowledged, "the run produced no events to lose"
    completions = [e for e in acknowledged if e[0] == "TASK_COMPLETED"]
    assert len(completions) == 12, f"expected 12 task completions, got {len(completions)}"

    fleet.kill_server()
    fleet.start_server(name="server-restarted")

    survived = _events(fleet.url, "default", "durability_probe", execution_id)

    assert survived == acknowledged, (
        "events acknowledged before the crash did not survive it: "
        f"{len(acknowledged)} acknowledged, {len(survived)} after restart"
    )
    status = httpx.get(
        f"{fleet.url}/workflows/default/durability_probe/status/{execution_id}",
        timeout=30,
    ).json()
    assert status["state"] == "COMPLETED"
    assert json.loads(json.dumps(status["output"])) is not None
