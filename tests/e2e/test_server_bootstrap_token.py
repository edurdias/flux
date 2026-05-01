"""E2E: ``flux server bootstrap-token`` reflects the active token, and the
server rejects worker registrations with a wrong or missing bootstrap token.

The shared E2E session sets ``FLUX_WORKERS__BOOTSTRAP_TOKEN`` explicitly so
the configured-value precedence path is what we exercise here. The command
is local-only (reads from on-disk file or local Configuration) and is not
piped through the running server.
"""

from __future__ import annotations

import subprocess

import httpx

from tests.e2e.conftest import PROJECT_ROOT


def _valid_registration_body(name: str) -> dict:
    """Build a body matching the WorkerRegistration / WorkerResourcesModel schemas."""
    return {
        "name": name,
        "runtime": {"os_name": "Linux", "os_version": "0.0", "python_version": "3.12"},
        "packages": [],
        "resources": {
            "cpu_total": 1.0,
            "cpu_available": 1.0,
            "memory_total": 1024.0,
            "memory_available": 1024.0,
            "disk_total": 1024.0,
            "disk_free": 1024.0,
            "gpus": [],
        },
        "labels": {},
    }


def test_server_bootstrap_token_prints_configured_value(cli):
    """When FLUX_WORKERS__BOOTSTRAP_TOKEN is set in the env, the CLI prints it."""
    result = subprocess.run(
        ["poetry", "run", "flux", "server", "bootstrap-token"],
        cwd=PROJECT_ROOT,
        env=cli._env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"command failed: {result.stderr}"
    assert result.stdout.strip() == cli._env["FLUX_WORKERS__BOOTSTRAP_TOKEN"]


def test_workers_register_rejects_wrong_bootstrap_token(cli):
    """An attacker who guesses a token must get 403 from /workers/register."""
    resp = httpx.post(
        f"{cli.server_url}/workers/register",
        json=_valid_registration_body("rogue-worker-1"),
        headers={"Authorization": "Bearer not-the-real-token"},
        timeout=10,
    )
    assert (
        resp.status_code == 403
    ), f"expected 403 for wrong bootstrap token; got {resp.status_code}: {resp.text}"
    assert "Invalid bootstrap token" in resp.text


def test_workers_register_rejects_missing_bootstrap_token(cli):
    """No Authorization header must be rejected (401 by the extractor, before
    the bootstrap-token compare even runs)."""
    resp = httpx.post(
        f"{cli.server_url}/workers/register",
        json=_valid_registration_body("rogue-worker-2"),
        timeout=10,
    )
    assert resp.status_code in (
        401,
        403,
    ), f"missing token must be rejected; got {resp.status_code}: {resp.text}"


def test_workers_register_rejects_non_bearer_scheme(cli):
    """Authorization header that is not Bearer must be rejected by the extractor."""
    resp = httpx.post(
        f"{cli.server_url}/workers/register",
        json=_valid_registration_body("rogue-worker-3"),
        headers={"Authorization": "Basic not-a-bearer"},
        timeout=10,
    )
    assert resp.status_code == 401


def test_workers_register_rejects_token_with_correct_prefix(cli):
    """A token sharing a prefix with the real one must still be rejected.

    Regression for the constant-time-compare fix: we use hmac.compare_digest
    so a partial match yields the same 403 as any other mismatch.
    """
    token = cli._env["FLUX_WORKERS__BOOTSTRAP_TOKEN"]
    near_match = token[: len(token) // 2] + "X" * (len(token) - len(token) // 2)
    resp = httpx.post(
        f"{cli.server_url}/workers/register",
        json=_valid_registration_body("rogue-worker-5"),
        headers={"Authorization": f"Bearer {near_match}"},
        timeout=10,
    )
    assert resp.status_code == 403


def test_workers_register_accepts_correct_bootstrap_token(cli):
    """The real configured token must be accepted by /workers/register."""
    token = cli._env["FLUX_WORKERS__BOOTSTRAP_TOKEN"]
    resp = httpx.post(
        f"{cli.server_url}/workers/register",
        json=_valid_registration_body("transient-e2e-worker"),
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    assert (
        resp.status_code == 200
    ), f"expected 200 for correct bootstrap token; got {resp.status_code}: {resp.text}"


def test_server_bootstrap_token_rotate_writes_new_file(cli, tmp_path):
    """`flux server bootstrap-token --rotate` must write a fresh token file.

    Runs in an isolated home dir (not the live E2E session's) to avoid
    desynchronizing the running server's token from the worker's. We override
    ``home`` and unset the configured token so the file path is what gets used.
    """
    isolated_env = {**cli._env}
    isolated_env["FLUX_HOME"] = str(tmp_path)
    isolated_env.pop("FLUX_WORKERS__BOOTSTRAP_TOKEN", None)

    result = subprocess.run(
        ["poetry", "run", "flux", "server", "bootstrap-token", "--rotate"],
        cwd=PROJECT_ROOT,
        env=isolated_env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"rotate failed: {result.stderr}"
    new_token = result.stdout.strip().splitlines()[-1]
    assert len(new_token) == 64
    persisted = (tmp_path / "bootstrap-token").read_text().strip()
    assert persisted == new_token


def test_server_bootstrap_token_no_token_anywhere_errors(cli, tmp_path):
    """If neither configured nor persisted, the CLI must error out (not crash)."""
    isolated_env = {**cli._env}
    isolated_env["FLUX_HOME"] = str(tmp_path)
    isolated_env.pop("FLUX_WORKERS__BOOTSTRAP_TOKEN", None)

    result = subprocess.run(
        ["poetry", "run", "flux", "server", "bootstrap-token"],
        cwd=PROJECT_ROOT,
        env=isolated_env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1
    assert "No bootstrap token found" in (result.stdout + result.stderr)
