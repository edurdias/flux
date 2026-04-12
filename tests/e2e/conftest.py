"""E2E test infrastructure — session fixture, CLI wrapper, markers."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
E2E_PORT = 19000
E2E_SERVER_URL = f"http://localhost:{E2E_PORT}"

# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.e2e


def pytest_collection_modifyitems(config, items):
    """Auto-skip @pytest.mark.ollama tests when Ollama is unavailable."""
    if not _probe_ollama():
        skip = pytest.mark.skip(reason="Ollama not available")
        for item in items:
            if "ollama" in item.keywords:
                item.add_marker(skip)


def _probe_ollama() -> bool:
    try:
        r = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ---------------------------------------------------------------------------
# FluxCLI wrapper
# ---------------------------------------------------------------------------


class FluxCLIError(Exception):
    def __init__(self, args: list[str], returncode: int, stderr: str):
        self.args_used = args
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"flux {' '.join(args)} failed (rc={returncode}): {stderr[:500]}")


class FluxCLI:
    """Thin wrapper over ``subprocess.run(["poetry", "run", "flux", ...])``."""

    def __init__(self, server_url: str, timeout: int = 60):
        self.server_url = server_url
        self.timeout = timeout
        self._env = {**os.environ}

    # -- low-level helpers ------------------------------------------------

    def _run(
        self,
        args: list[str],
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess:
        cmd = ["poetry", "run", "flux", *args]
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout or self.timeout,
            cwd=PROJECT_ROOT,
            env=self._env,
        )

    def _server(
        self,
        args: list[str],
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess:
        """Run a command that talks to the server (appends --server-url)."""
        return self._run([*args, "--server-url", self.server_url], timeout=timeout)

    def _server_json(self, args: list[str], **kw) -> Any:
        r = self._server(args, **kw)
        if r.returncode != 0:
            raise FluxCLIError(args, r.returncode, r.stderr)
        return json.loads(r.stdout)

    def _json(self, args: list[str], **kw) -> Any:
        r = self._run(args, **kw)
        if r.returncode != 0:
            raise FluxCLIError(args, r.returncode, r.stderr)
        return json.loads(r.stdout)

    def _server_ok(self, args: list[str], **kw) -> subprocess.CompletedProcess:
        """Run a server command and assert rc=0. Returns the CompletedProcess."""
        r = self._server(args, **kw)
        if r.returncode != 0:
            raise FluxCLIError(args, r.returncode, r.stderr)
        return r

    def _ok(self, args: list[str], **kw) -> subprocess.CompletedProcess:
        r = self._run(args, **kw)
        if r.returncode != 0:
            raise FluxCLIError(args, r.returncode, r.stderr)
        return r

    # -- workflow commands -------------------------------------------------

    def register(self, file: str) -> dict | list:
        return self._server_json(
            ["workflow", "register", str(file), "--format", "json"],
        )

    def run(
        self,
        ref: str,
        input: str = "null",
        mode: str = "sync",
        timeout: int = 60,
    ) -> dict:
        return self._server_json(
            ["workflow", "run", ref, input, "--mode", mode],
            timeout=timeout,
        )

    def show(self, ref: str) -> dict:
        return self._server_json(["workflow", "show", ref, "--format", "json"])

    def delete(self, ref: str) -> None:
        self._server_ok(["workflow", "delete", ref, "--force"])

    def versions(self, ref: str) -> list:
        return self._server_json(
            ["workflow", "versions", ref, "--format", "json"],
        )

    def status(self, ref: str, exec_id: str) -> dict:
        return self._server_json(["workflow", "status", ref, exec_id])

    def resume(self, ref: str, exec_id: str, input: str | None = None) -> dict:
        args = ["workflow", "resume", ref, exec_id, input if input is not None else "null"]
        return self._server_json(args)

    def cancel(self, ref: str, exec_id: str) -> subprocess.CompletedProcess:
        return self._server_ok(["workflow", "cancel", ref, exec_id])

    def list_workflows(self, namespace: str | None = None) -> list:
        args = ["workflow", "list", "--format", "json"]
        if namespace:
            args.extend(["--namespace", namespace])
        return self._server_json(args)

    def list_namespaces(self) -> list:
        return self._server_json(
            ["workflow", "list-namespaces", "--format", "json"],
        )

    # -- execution commands ------------------------------------------------

    def execution_list(
        self,
        namespace: str | None = None,
        workflow: str | None = None,
    ) -> dict:
        args = ["execution", "list", "--format", "json"]
        if workflow:
            args.extend(["--workflow", workflow])
        elif namespace:
            args.extend(["--namespace", namespace])
        return self._server_json(args)

    def execution_show(self, exec_id: str) -> dict:
        return self._server_json(["execution", "show", exec_id])

    # -- schedule commands -------------------------------------------------

    def schedule_create(
        self,
        workflow_ref: str,
        name: str,
        cron: str | None = None,
        interval_minutes: int | None = None,
    ) -> dict:
        args = ["schedule", "create", workflow_ref, name]
        if cron:
            args.extend(["--cron", cron])
        elif interval_minutes:
            args.extend(["--interval-minutes", str(interval_minutes)])
        args.extend(["--format", "json"])
        return self._server_json(args)

    def schedule_list(self) -> list:
        return self._server_json(["schedule", "list", "--format", "json"])

    def schedule_show(self, schedule_id: str) -> dict:
        return self._server_json(
            ["schedule", "show", schedule_id, "--format", "json"],
        )

    def schedule_pause(self, schedule_id: str) -> None:
        self._server_ok(["schedule", "pause", schedule_id])

    def schedule_resume(self, schedule_id: str) -> None:
        self._server_ok(["schedule", "resume", schedule_id])

    def schedule_delete(self, schedule_id: str) -> None:
        self._server_ok(["schedule", "delete", schedule_id, "--yes"])

    def schedule_history(self, schedule_id: str) -> dict:
        return self._server_json(
            ["schedule", "history", schedule_id, "--format", "json"],
        )

    # -- worker commands ---------------------------------------------------

    def health(self) -> dict:
        return self._server_json(["health", "--format", "json"])

    def worker_list(self) -> list:
        return self._server_json(["worker", "list", "--format", "json"])

    def worker_show(self, name: str) -> dict:
        return self._server_json(["worker", "show", name])

    # -- admin: roles ------------------------------------------------------

    def role_create(self, name: str, permissions: list[str]) -> dict:
        args = ["roles", "create", name]
        for p in permissions:
            args.extend(["--permissions", p])
        args.extend(["--format", "json"])
        return self._server_json(args)

    def role_list(self) -> list:
        return self._server_json(["roles", "list", "--format", "json"])

    def role_show(self, name: str) -> dict:
        return self._server_json(["roles", "show", name, "--format", "json"])

    def role_delete(self, name: str) -> None:
        self._server_ok(["roles", "delete", name])

    def role_clone(self, source: str, new_name: str) -> dict:
        return self._server_json(
            ["roles", "clone", source, "--name", new_name, "--format", "json"],
        )

    def role_update(
        self,
        name: str,
        add: list[str] | None = None,
        remove: list[str] | None = None,
    ) -> dict:
        args = ["roles", "update", name]
        for p in add or []:
            args.extend(["--add-permissions", p])
        for p in remove or []:
            args.extend(["--remove-permissions", p])
        args.extend(["--format", "json"])
        return self._server_json(args)

    # -- admin: principals -------------------------------------------------

    def principal_create(
        self,
        subject: str,
        principal_type: str = "service_account",
        roles: list[str] | None = None,
        display_name: str | None = None,
    ) -> dict:
        args = ["principals", "create", subject, "--type", principal_type]
        for r in roles or []:
            args.extend(["--role", r])
        if display_name:
            args.extend(["--display-name", display_name])
        args.extend(["--format", "json"])
        return self._server_json(args)

    def principal_list(self) -> list:
        return self._server_json(
            ["principals", "list", "--format", "json"],
        )

    def principal_show(self, subject: str) -> dict:
        return self._server_json(
            ["principals", "show", subject, "--format", "json"],
        )

    def principal_delete(self, subject: str) -> None:
        self._server_ok(
            ["principals", "delete", subject, "--force", "--yes"],
        )

    def principal_enable(self, subject: str) -> None:
        self._server_ok(["principals", "enable", subject])

    def principal_disable(self, subject: str) -> None:
        self._server_ok(["principals", "disable", subject])

    def principal_grant(self, subject: str, role: str) -> None:
        self._server_ok(
            ["principals", "grant", subject, "--role", role],
        )

    def principal_revoke(self, subject: str, role: str) -> None:
        self._server_ok(
            ["principals", "revoke", subject, "--role", role],
        )

    def principal_create_key(
        self,
        subject: str,
        key_name: str,
    ) -> dict:
        return self._server_json(
            [
                "principals",
                "create-key",
                subject,
                "--key-name",
                key_name,
                "--format",
                "json",
            ],
        )

    def principal_list_keys(self, subject: str) -> list:
        return self._server_json(
            ["principals", "list-keys", subject, "--format", "json"],
        )

    def principal_revoke_key(self, subject: str, key_name: str) -> None:
        self._server_ok(
            ["principals", "revoke-key", subject, "--key-name", key_name],
        )

    # -- auth commands -----------------------------------------------------

    def auth_status(self) -> dict:
        return self._server_json(["auth", "status", "--format", "json"])

    def auth_permissions(self) -> dict:
        return self._server_json(
            ["auth", "permissions", "--format", "json"],
        )

    # -- secrets (local, no --server-url) ----------------------------------

    def secrets_set(self, name: str, value: str) -> None:
        self._ok(["secrets", "set", name, value])

    def secrets_get(self, name: str) -> dict:
        return self._json(["secrets", "get", name, "--format", "json"])

    def secrets_list(self) -> dict:
        return self._json(["secrets", "list", "--format", "json"])

    def secrets_remove(self, name: str) -> None:
        self._ok(["secrets", "remove", name])

    # -- polling helpers ---------------------------------------------------

    def wait_for_state(
        self,
        ref: str,
        exec_id: str,
        target: str,
        timeout: int = 60,
        interval: int = 2,
    ) -> dict:
        """Poll ``workflow status`` until the target state or timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            s = self.status(ref, exec_id)
            if s.get("state") == target:
                return s
            time.sleep(interval)
        raise TimeoutError(
            f"Workflow {ref} exec {exec_id} did not reach {target} "
            f"within {timeout}s (last state: {s.get('state')})",
        )

    def run_async_and_wait(
        self,
        ref: str,
        input: str = "null",
        target: str = "COMPLETED",
        timeout: int = 60,
    ) -> dict:
        """Run async, then poll to target state. Returns final status."""
        r = self.run(ref, input, mode="async", timeout=30)
        exec_id = r["execution_id"]
        return self.wait_for_state(ref, exec_id, target, timeout=timeout)


# ---------------------------------------------------------------------------
# Session fixture — server + worker lifecycle
# ---------------------------------------------------------------------------


def _kill_process(proc: subprocess.Popen, name: str):
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


@pytest.fixture(scope="session")
def cli(tmp_path_factory):
    """Start a Flux server + worker, yield a FluxCLI, tear down on exit."""
    tmp = tmp_path_factory.mktemp("e2e")
    db_path = tmp / "flux.db"
    log_dir = tmp / "logs"
    log_dir.mkdir()

    env = {
        **os.environ,
        "FLUX_SERVER_PORT": str(E2E_PORT),
        "FLUX_DATABASE_URL": f"sqlite:///{db_path}",
        "FLUX_WORKERS__SERVER_URL": E2E_SERVER_URL,
        "FLUX_SECURITY__AUTH__ENABLED": "false",
    }

    # -- start server -----------------------------------------------------
    srv = subprocess.Popen(
        ["poetry", "run", "flux", "start", "server", "--port", str(E2E_PORT)],
        stdout=open(log_dir / "server.log", "w"),
        stderr=subprocess.STDOUT,
        cwd=PROJECT_ROOT,
        env=env,
    )

    # Poll health
    deadline = time.monotonic() + 30
    healthy = False
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{E2E_SERVER_URL}/health", timeout=2)
            if r.status_code == 200:
                healthy = True
                break
        except httpx.ConnectError:
            pass
        time.sleep(1)
    if not healthy:
        _kill_process(srv, "server")
        pytest.fail(
            f"Flux server did not become healthy on port {E2E_PORT} within 30s. "
            f"Check {log_dir / 'server.log'}",
        )

    # -- start worker -----------------------------------------------------
    wkr = subprocess.Popen(
        [
            "poetry",
            "run",
            "flux",
            "start",
            "worker",
            "e2e-worker",
            "--server-url",
            E2E_SERVER_URL,
        ],
        stdout=open(log_dir / "worker.log", "w"),
        stderr=subprocess.STDOUT,
        cwd=PROJECT_ROOT,
        env=env,
    )

    # Poll workers endpoint
    deadline = time.monotonic() + 30
    connected = False
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{E2E_SERVER_URL}/workers", timeout=2)
            if r.status_code == 200 and len(r.json()) > 0:
                connected = True
                break
        except httpx.ConnectError:
            pass
        time.sleep(1)
    if not connected:
        _kill_process(wkr, "worker")
        _kill_process(srv, "server")
        pytest.fail(f"Worker did not connect within 30s. Check {log_dir / 'worker.log'}")

    # -- yield CLI instance -----------------------------------------------
    flux_cli = FluxCLI(server_url=E2E_SERVER_URL)
    flux_cli._env = env  # subprocess inherits the test env

    yield flux_cli

    # -- teardown ---------------------------------------------------------
    _kill_process(wkr, "worker")
    _kill_process(srv, "server")

    if not os.environ.get("FLUX_E2E_KEEP_LOGS"):
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
