"""Server + worker subprocess lifecycle for the perf suite.

Models the tests/e2e pattern without importing it: a throwaway SQLite
database, a free port, auth disabled, bootstrap token and encryption key
seeded. Exposes raw-HTTP helpers (register / run / status / cancel) so the
measurement path never shells out to the CLI.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

# fixtures/harness/env.py -> perf -> tests -> repo root
PROJECT_ROOT = Path(__file__).resolve().parents[4]

TERMINAL_STATES = {"COMPLETED", "FAILED", "CANCELLED"}


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def docker_available() -> bool:
    """True when the docker-airgapped runner can actually run here."""
    if shutil.which("docker") is None:
        return False
    try:
        r = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _kill(proc: subprocess.Popen | None):
    if proc is None or proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


class FluxPerfEnv:
    """One Flux server + one worker, as subprocesses, plus HTTP helpers."""

    def __init__(
        self,
        workdir: Path,
        port: int | None = None,
        database_url: str | None = None,
        worker_name: str = "perf-worker",
        env_overrides: dict[str, str] | None = None,
    ):
        self.workdir = workdir
        self.port = port or free_port()
        self.server_url = f"http://localhost:{self.port}"
        self.db_path = workdir / "flux.db"
        self.database_url = database_url or f"sqlite:///{self.db_path}"
        self.worker_name = worker_name
        self.log_dir = workdir / "logs"
        self.server_proc: subprocess.Popen | None = None
        self.worker_proc: subprocess.Popen | None = None
        self._log_files: list = []
        self.env = {
            **os.environ,
            "FLUX_SERVER_PORT": str(self.port),
            "FLUX_DATABASE_URL": self.database_url,
            "FLUX_WORKERS__SERVER_URL": self.server_url,
            "FLUX_WORKERS__BOOTSTRAP_TOKEN": "perf-test-bootstrap-token",
            "FLUX_SECURITY__AUTH__ENABLED": "false",
            "FLUX_SECURITY__AUTH__ALLOW_ANONYMOUS": "true",
            "FLUX_SECURITY__ENCRYPTION__ENCRYPTION_KEY": "perf-test-encryption-key",
            **(env_overrides or {}),
        }
        self.extra_workers: list[subprocess.Popen] = []
        self._http = httpx.Client(base_url=self.server_url, timeout=30)

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        self.log_dir.mkdir(parents=True, exist_ok=True)
        srv_log = open(self.log_dir / "server.log", "w")
        self._log_files.append(srv_log)
        self.server_proc = subprocess.Popen(
            ["poetry", "run", "flux", "start", "server", "--port", str(self.port)],
            stdout=srv_log,
            stderr=subprocess.STDOUT,
            cwd=PROJECT_ROOT,
            env=self.env,
        )
        self._wait_healthy()

        wkr_log = open(self.log_dir / "worker.log", "w")
        self._log_files.append(wkr_log)
        self.worker_proc = subprocess.Popen(
            [
                "poetry",
                "run",
                "flux",
                "start",
                "worker",
                self.worker_name,
                "--server-url",
                self.server_url,
            ],
            stdout=wkr_log,
            stderr=subprocess.STDOUT,
            cwd=PROJECT_ROOT,
            env=self.env,
        )
        self._wait_worker_connected()
        return self

    def stop(self):
        for proc in self.extra_workers:
            _kill(proc)
        _kill(self.worker_proc)
        _kill(self.server_proc)
        self._http.close()
        for f in self._log_files:
            f.close()

    def start_extra_worker(
        self,
        name: str,
        server_url: str | None = None,
        timeout: float = 60.0,
    ) -> subprocess.Popen:
        """Start an additional worker (optionally via a proxy server URL)."""
        log = open(self.log_dir / f"worker-{name}.log", "w")
        self._log_files.append(log)
        proc = subprocess.Popen(
            [
                "poetry",
                "run",
                "flux",
                "start",
                "worker",
                name,
                "--server-url",
                server_url or self.server_url,
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=PROJECT_ROOT,
            env=self.env,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                r = self._http.get("/workers")
                if r.status_code == 200 and any(w["name"] == name for w in r.json()):
                    self.extra_workers.append(proc)
                    return proc
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        _kill(proc)
        raise RuntimeError(f"Extra worker '{name}' did not connect within {timeout}s")

    def kill_worker(self, proc: subprocess.Popen, force: bool = False):
        """Stop a worker process; force=True is an unclean SIGKILL (T6b)."""
        if force:
            proc.kill()
            proc.wait(timeout=10)
        else:
            _kill(proc)
        if proc in self.extra_workers:
            self.extra_workers.remove(proc)

    def _wait_healthy(self, timeout: float = 60.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self._http.get("/health").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        tail = self._log_tail("server.log")
        self.stop()
        raise RuntimeError(
            f"Flux server not healthy on port {self.port} within {timeout}s.\n"
            f"--- server.log tail ---\n{tail}",
        )

    def _wait_worker_connected(self, timeout: float = 60.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                r = self._http.get("/workers")
                if r.status_code == 200 and any(w["name"] == self.worker_name for w in r.json()):
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        tail = self._log_tail("worker.log")
        self.stop()
        raise RuntimeError(
            f"Worker '{self.worker_name}' did not connect within {timeout}s.\n"
            f"--- worker.log tail ---\n{tail}",
        )

    def _log_tail(self, name: str, n: int = 2000) -> str:
        try:
            return (self.log_dir / name).read_text()[-n:]
        except OSError:
            return f"<no {name}>"

    # -- HTTP helpers --------------------------------------------------------

    def register(self, source_path: Path) -> Any:
        """Upload a workflow source file to the catalog."""
        with open(source_path, "rb") as f:
            r = self._http.post(
                "/workflows",
                files={"file": (source_path.name, f, "text/x-python")},
            )
        r.raise_for_status()
        return r.json()

    def run_async(self, namespace: str, name: str, input: Any) -> dict:
        r = self._http.post(
            f"/workflows/{namespace}/{name}/run/async",
            json=input,
        )
        r.raise_for_status()
        return r.json()

    def status(
        self,
        namespace: str,
        name: str,
        execution_id: str,
        detailed: bool = False,
    ) -> dict:
        r = self._http.get(
            f"/workflows/{namespace}/{name}/status/{execution_id}",
            params={"detailed": detailed},
        )
        r.raise_for_status()
        return r.json()

    def cancel(self, namespace: str, name: str, execution_id: str) -> dict:
        r = self._http.get(f"/workflows/{namespace}/{name}/cancel/{execution_id}")
        r.raise_for_status()
        return r.json()

    def wait_for_terminal(
        self,
        namespace: str,
        name: str,
        execution_id: str,
        timeout: float = 120.0,
        interval: float = 0.5,
    ) -> dict:
        deadline = time.monotonic() + timeout
        last: dict = {}
        while time.monotonic() < deadline:
            last = self.status(namespace, name, execution_id)
            if last.get("state") in TERMINAL_STATES:
                return last
            time.sleep(interval)
        raise TimeoutError(
            f"Execution {execution_id} not terminal within {timeout}s "
            f"(last state: {last.get('state')})",
        )

    def measure_http_rtt(self, samples: int = 20) -> float:
        """Median localhost round-trip of GET /health, in seconds.

        Recorded into every run so throughput ceilings (≈50 frames/RTT per
        execution, see PLAN.md §0b) transfer to other machines.
        """
        times = []
        for _ in range(samples):
            t0 = time.perf_counter()
            self._http.get("/health")
            times.append(time.perf_counter() - t0)
        return statistics.median(times)
