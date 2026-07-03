"""Runner that executes each workflow in its own Docker container.

Speaks the exact same stdio frame protocol as the subprocess runner —
``docker run -i`` attaches the container's stdin/stdout, and ``--sig-proxy``
(the docker CLI default without a TTY) forwards SIGTERM for graceful
cancellation — so the container holds no credentials either: checkpoints,
progress, secrets, and configs all flow through the parent worker.

The image must have ``flux-core`` installed at a version compatible with the
worker (the child entrypoint and context wire format must match). Workers
enable it explicitly:

    [flux.workers]
    runners = ["inprocess", "subprocess", "docker"]
    docker_image = "my-registry/flux-workflows:1.2.3"
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from typing import TYPE_CHECKING
from uuid import uuid4

from flux.runners.subprocess_runner import _STREAM_LIMIT, SubprocessRunner
from flux.utils import get_logger

if TYPE_CHECKING:
    from flux.worker import WorkflowExecutionRequest

logger = get_logger(__name__)


class DockerRunner(SubprocessRunner):
    name = "docker"

    def __init__(
        self,
        image: str,
        term_grace: float = 10.0,
        network: str = "",
        memory: str = "",
        cpus: float = 0.0,
        extra_args: list[str] | None = None,
    ):
        super().__init__(term_grace=term_grace)
        if not image:
            raise ValueError(
                "[flux.workers] docker_image must be set when the 'docker' runner is enabled",
            )
        self._image = image
        self._network = network
        self._memory = memory
        self._cpus = cpus
        self._extra_args = list(extra_args or [])
        # docker-CLI pid -> container name, for docker-kill on force kill.
        self._containers: dict[int, str] = {}
        self._verify_docker_available()

    @staticmethod
    def _verify_docker_available():
        """Fail at worker startup, not at first dispatch."""
        if shutil.which("docker") is None:
            raise ValueError(
                "The 'docker' runner is enabled but the docker CLI is not on PATH",
            )
        probe = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if probe.returncode != 0:
            raise ValueError(
                "The 'docker' runner is enabled but the Docker daemon is "
                f"unreachable: {probe.stderr.strip() or probe.stdout.strip()}",
            )
        logger.info(f"Docker runner ready (server {probe.stdout.strip()})")

    def _container_name(self, execution_id: str) -> str:
        # Unique per attempt: a crashed execution can be re-dispatched to this
        # worker while its previous --rm container is still being removed.
        return f"flux-exec-{execution_id[:24]}-{uuid4().hex[:6]}"

    def _build_command(self, container_name: str) -> list[str]:
        command = ["docker", "run", "-i", "--rm", "--name", container_name]
        if self._network:
            command += ["--network", self._network]
        if self._memory:
            command += ["--memory", self._memory]
        if self._cpus:
            command += ["--cpus", str(self._cpus)]
        command += self._extra_args
        command += [self._image, "python", "-m", "flux.runners.child"]
        return command

    async def _spawn(self, request: WorkflowExecutionRequest):
        container_name = self._container_name(request.context.execution_id)
        proc = await asyncio.create_subprocess_exec(
            *self._build_command(container_name),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=_STREAM_LIMIT,
        )
        self._containers[proc.pid] = container_name
        logger.debug(
            f"Execution {request.context.execution_id} running in container {container_name}",
        )
        return proc

    async def _force_kill(self, proc):
        # Killing the docker CLI alone would orphan the container; kill the
        # container (which also ends the attached CLI process).
        container_name = self._containers.get(proc.pid)
        if container_name:
            killer = await asyncio.create_subprocess_exec(
                "docker",
                "kill",
                container_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
        proc.kill()

    def _reap(self, proc):
        self._containers.pop(proc.pid, None)
