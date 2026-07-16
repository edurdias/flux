"""E2E tests for service sockets: the full server -> dispatch -> worker ->
subprocess child -> UDS sidecar path.

A real HTTP-over-UDS sidecar runs in a background thread; the worker
process carries ``FLUX_SERVICE_SOCKETS`` in its environment, the
subprocess child inherits it through the sanitized-env passthrough (the
same variable an airgapped container receives via the runner's ``--env``),
and the workflow's ``service_client`` call crosses the socket for real.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
PROJECT_ROOT = Path(__file__).parents[2]

pytestmark = pytest.mark.e2e


class _UdsSidecar:
    """Minimal HTTP/1.1 sidecar on a Unix socket, run in its own thread."""

    def __init__(self, body: bytes):
        # Keep the socket path short: UDS paths are capped near 108 chars
        # and pytest tmp factories produce long ones.
        self._dir = tempfile.mkdtemp(prefix="flux-svc-")
        self.socket_path = str(Path(self._dir) / "service.sock")
        self._body = body
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.requests_served = 0

    def _run(self):
        async def main():
            async def handle(reader, writer):
                while True:
                    line = await reader.readline()
                    if not line or line == b"\r\n":
                        break
                self.requests_served += 1
                writer.write(
                    b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n"
                    b"content-length: " + str(len(self._body)).encode() + b"\r\n\r\n" + self._body,
                )
                await writer.drain()
                writer.close()

            server = await asyncio.start_unix_server(handle, path=self.socket_path)
            self._ready.set()
            while not self._stop.is_set():
                await asyncio.sleep(0.1)
            server.close()
            await server.wait_closed()

        asyncio.run(main())

    def __enter__(self):
        self._thread.start()
        assert self._ready.wait(timeout=10), "UDS sidecar failed to start"
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=10)


def test_service_call_roundtrip_through_dispatch(cli):
    """A dispatched execution reaches a warm sidecar over its socket and
    the response comes back through the normal checkpoint path."""
    with _UdsSidecar(b'{"label": "sealed-ok"}') as sidecar:
        worker = cli.start_worker(
            "svc-echo-worker",
            labels={"svc": "echo"},
            env={"FLUX_SERVICE_SOCKETS": json.dumps({"echo": sidecar.socket_path})},
        )
        try:
            cli.register(str(FIXTURES / "service_socket_workflow.py"))
            r = cli.run("service_roundtrip", "null", timeout=60)
            assert r["state"] == "COMPLETED"
            assert r.get("current_worker") == "svc-echo-worker"
            assert r["output"] == {"label": "sealed-ok"}
            assert sidecar.requests_served >= 1
        finally:
            cli.stop_worker(worker)


def test_missing_service_fails_with_a_pointer(cli):
    """An ungranted service is a normal task failure whose message names
    the config key and the affinity hint."""
    cli.register(str(FIXTURES / "service_socket_workflow.py"))
    r = cli.run("service_missing", "null", timeout=60)
    assert r["state"] == "FAILED"
    output = str(r.get("output"))
    assert "airgapped_service_sockets" in output
    assert "flux.service.not-granted-anywhere" in output


def test_reserved_flux_label_rejected_at_worker_startup(cli):
    """User labels under the reserved flux. prefix must fail worker
    startup — they would spoof service-capability grants."""
    proc = subprocess.Popen(
        [
            "poetry",
            "run",
            "flux",
            "start",
            "worker",
            "spoofing-worker",
            "--server-url",
            cli.server_url,
            "--label",
            "flux.service.fake=true",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        cwd=PROJECT_ROOT,
        env=dict(cli._env),
        text=True,
    )
    try:
        deadline = time.monotonic() + 45
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.5)
        assert proc.poll() is not None, "worker with reserved label should exit"
        assert proc.returncode != 0
        stderr = proc.stderr.read() if proc.stderr else ""
        assert "reserved 'flux.' prefix" in stderr
        assert all(w["name"] != "spoofing-worker" for w in cli.worker_list())
    finally:
        if proc.poll() is None:
            proc.kill()
