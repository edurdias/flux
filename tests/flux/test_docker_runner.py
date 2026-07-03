"""Tests for the Docker runner.

Unit tests (command construction, config validation) run everywhere. The
integration test executes a real container and only runs when
``FLUX_TEST_DOCKER_IMAGE`` names an image with flux-core installed — build
one locally and run:

    FLUX_TEST_DOCKER_IMAGE=my-flux-image pytest tests/flux/test_docker_runner.py
"""

from __future__ import annotations

import base64
import os
import textwrap
from unittest.mock import MagicMock, patch

import pytest

from flux.runners.docker import DockerRunner

DOCKER_TEST_IMAGE = os.environ.get("FLUX_TEST_DOCKER_IMAGE")


def make_runner(**kwargs) -> DockerRunner:
    with patch.object(DockerRunner, "_verify_docker_available"):
        return DockerRunner(image=kwargs.pop("image", "flux:test"), **kwargs)


class TestDockerCommand:
    def test_command_includes_image_and_child_entrypoint(self):
        runner = make_runner()
        command = runner._build_command("flux-exec-abc")

        assert command[:6] == ["docker", "run", "-i", "--rm", "--name", "flux-exec-abc"]
        assert command[-3:] == ["flux:test", "python", "-m"] or command[-4:] == [
            "flux:test",
            "python",
            "-m",
            "flux.runners.child",
        ]

    def test_command_includes_limits_network_and_extra_args(self):
        runner = make_runner(
            network="host",
            memory="512m",
            cpus=1.5,
            extra_args=["--env", "TZ=UTC"],
        )
        command = runner._build_command("c")

        assert ["--network", "host"] == command[command.index("--network") :][:2]
        assert ["--memory", "512m"] == command[command.index("--memory") :][:2]
        assert ["--cpus", "1.5"] == command[command.index("--cpus") :][:2]
        env_at = command.index("--env")
        assert command[env_at : env_at + 2] == ["--env", "TZ=UTC"]
        # Extra args come before the image so they configure the container.
        assert env_at < command.index("flux:test")

    def test_container_names_unique_per_attempt(self):
        runner = make_runner()
        names = {runner._container_name("exec-1") for _ in range(5)}
        assert len(names) == 5
        assert all(n.startswith("flux-exec-exec-1") for n in names)

    def test_missing_image_rejected_at_startup(self):
        with pytest.raises(ValueError, match="docker_image must be set"):
            with patch.object(DockerRunner, "_verify_docker_available"):
                DockerRunner(image="")

    def test_create_runners_wires_docker_config(self):
        from flux.runners import create_runners

        config = MagicMock(
            module_cache_ttl=0,
            module_cache_max_size=8,
            subprocess_term_grace=5.0,
            subprocess_memory_limit=0,
            docker_image="registry/flux:1",
            docker_network="host",
            docker_memory="256m",
            docker_cpus=2.0,
            docker_extra_args=["--user", "1000"],
        )
        with patch.object(DockerRunner, "_verify_docker_available"):
            runners = create_runners(["docker"], config)

        command = runners["docker"]._build_command("c")
        assert "registry/flux:1" in command
        assert "--user" in command


@pytest.mark.skipif(
    not DOCKER_TEST_IMAGE,
    reason="FLUX_TEST_DOCKER_IMAGE not set (needs an image with flux-core installed)",
)
@pytest.mark.asyncio
async def test_docker_runner_executes_workflow_in_container():
    from flux.domain.execution_context import ExecutionContext
    from flux.runners.base import RunnerHooks
    from flux.worker import WorkflowDefinition, WorkflowExecutionRequest

    source = textwrap.dedent(
        """
        from flux import ExecutionContext, task, workflow

        @task.with_options(secret_requests=["API_KEY"])
        async def secret_len(secrets: dict = {}) -> int:
            return len(secrets["API_KEY"])

        @workflow
        async def docker_wf(ctx: ExecutionContext[int]):
            return ctx.input * 2 + await secret_len()
        """,
    )
    checkpoints = []

    async def checkpoint(ctx):
        checkpoints.append(ctx)

    async def get_secrets(names):
        return {n: "0123456789" for n in names}

    async def get_configs(names):
        return {}

    request = WorkflowExecutionRequest(
        workflow=WorkflowDefinition(
            id="default/docker_wf",
            namespace="default",
            name="docker_wf",
            version=1,
            source=base64.b64encode(source.encode()).decode(),
        ),
        context=ExecutionContext(
            workflow_id="default/docker_wf",
            workflow_namespace="default",
            workflow_name="docker_wf",
            input=16,
        ),
    )
    runner = DockerRunner(image=DOCKER_TEST_IMAGE, term_grace=10)
    result = await runner.execute(
        request,
        RunnerHooks(checkpoint=checkpoint, get_secrets=get_secrets, get_configs=get_configs),
    )

    assert result.has_finished and not result.has_failed
    # 16 * 2 + len("0123456789") — the secret resolved through the parent pipe.
    assert result.output == 42
    assert checkpoints and checkpoints[-1].has_finished
