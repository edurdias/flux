"""E2E: airgapped_container_cli is validated at worker startup.

The airgapped runner can drive docker, podman, or nerdctl; anything else
must fail the worker process fast (before it ever registers), with an
error naming the config key. Running an actual podman/nerdctl container
needs a container runtime and is covered by the gated integration tests
in tests/flux/test_docker_runner.py.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def test_unknown_container_cli_fails_worker_startup(cli):
    env = {
        **cli._env,
        "FLUX_WORKERS__RUNNERS": '["subprocess", "docker-airgapped"]',
        "FLUX_WORKERS__DEFAULT_RUNNER": "subprocess",
        "FLUX_WORKERS__DOCKER_IMAGE": "flux:e2e-test",
        "FLUX_WORKERS__AIRGAPPED_CONTAINER_CLI": "containerctl",
    }
    r = subprocess.run(
        [
            "poetry",
            "run",
            "flux",
            "start",
            "worker",
            "bad-cli-worker",
            "--server-url",
            cli.server_url,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=PROJECT_ROOT,
        env=env,
    )

    assert r.returncode != 0
    output = r.stderr + r.stdout
    assert "airgapped_container_cli" in output
    assert "containerctl" in output
    # The worker never registered.
    assert all(w["name"] != "bad-cli-worker" for w in cli.worker_list())


@pytest.mark.skipif(
    os.environ.get("FLUX_TEST_PODMAN") != "1",
    reason="FLUX_TEST_PODMAN=1 not set (needs a working rootless podman)",
)
def test_podman_worker_starts_and_registers(cli):
    """Opt-in: with rootless podman present, a podman-backed airgapped
    worker starts, validates the CLI, and registers."""
    worker = cli.start_worker(
        "podman-worker",
        env={
            "FLUX_WORKERS__RUNNERS": '["subprocess", "docker-airgapped"]',
            "FLUX_WORKERS__DEFAULT_RUNNER": "subprocess",
            "FLUX_WORKERS__DOCKER_IMAGE": os.environ.get(
                "FLUX_TEST_DOCKER_IMAGE",
                "flux:e2e-test",
            ),
            "FLUX_WORKERS__AIRGAPPED_CONTAINER_CLI": "podman",
        },
    )
    try:
        assert any(w["name"] == "podman-worker" for w in cli.worker_list())
    finally:
        cli.stop_worker(worker)
