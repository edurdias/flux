"""Runner that executes each workflow in its own Docker container.

Speaks the exact same stdio frame protocol as the subprocess runner —
``docker run -i`` attaches the container's stdin/stdout, and ``--sig-proxy``
(the docker CLI default without a TTY) forwards SIGTERM for graceful
cancellation — so the container holds no credentials either: checkpoints,
progress, secrets, and configs all flow through the parent worker.

The image must have ``flux-core`` installed at a version compatible with the
worker (the child entrypoint and context wire format must match) — the
official Flux image satisfies this when its tag matches the worker's
flux-core version (see DOCKER.md). Workers enable it explicitly:

    [flux.workers]
    runners = ["inprocess", "subprocess", "docker"]
    docker_image = "my-registry/flux-workflows:1.2.3"
"""

from __future__ import annotations

import asyncio
import contextlib
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
        execution_timeout: float = 0,
    ):
        super().__init__(term_grace=term_grace, execution_timeout=execution_timeout)
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
        """Template method. Sections in order: fixed prefix, config-derived
        resource args, operator extra_args, then ``_locked_args()`` — LAST so
        docker's last-wins flag parsing structurally favors a hardened
        profile over anything an operator (or config-file attacker) adds."""
        command = ["docker", "run", "-i", "--rm", "--name", container_name]
        command += self._resource_args()
        command += self._extra_args
        command += self._locked_args()
        command += [self._image, "python", "-m", "flux.runners.child"]
        return command

    def _resource_args(self) -> list[str]:
        args: list[str] = []
        if self._network:
            args += ["--network", self._network]
        if self._memory:
            args += ["--memory", self._memory]
        if self._cpus:
            args += ["--cpus", str(self._cpus)]
        return args

    def _locked_args(self) -> list[str]:
        """Non-configurable flags a hardened subclass emits; none here."""
        return []

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
        with contextlib.suppress(ProcessLookupError):
            # Best-effort: docker kill usually already ended the CLI process.
            proc.kill()

    def _reap(self, proc):
        self._containers.pop(proc.pid, None)


# extra_args must not be able to re-open what the airgapped profile closed:
# network, host namespaces, devices, mounts, privileges, DNS. A denylist (not
# an allowlist) on purpose — benign operator knobs (--env, --user, --tmpfs)
# can't be enumerated, while the guarantees to protect can.
_AIRGAPPED_VETOED_FLAGS = frozenset(
    {
        "--network",
        "--net",
        "--privileged",
        "--cap-add",
        "--device",
        "--volume",
        "-v",
        "--mount",
        "--volumes-from",
        "--pid",
        "--ipc",
        "--uts",
        "--userns",
        "--security-opt",
        "--group-add",
        "--add-host",
        "--dns",
        "--dns-search",
        "--dns-option",
        "--link",
        "--publish",
        "-p",
        "--publish-all",
        "-P",
        "--expose",
        "--sysctl",
        "--cgroup-parent",
        "--cgroupns",
        "--device-cgroup-rule",
        "--oom-kill-disable",
    },
)


class AirgappedDockerRunner(DockerRunner):
    """Docker runner with a locked isolation profile for untrusted workflows.

    The container's only capability channel is the stdio protocol to the
    parent worker (where every secret/config/approval/checkpoint is
    permission-checked); the profile removes everything else: no network
    (which also makes ``pip install`` impossible — the image's packages are
    the whole world), read-only rootfs with a size-capped tmpfs ``/tmp``,
    no capabilities, no privilege escalation, pids/memory/cpu limits, and a
    wall-clock ceiling that fails the execution terminally (see
    ``ExecutionTimedOut``) instead of re-dispatching a deterministic repeat.

    The profile is emitted from code (``_locked_args``), after operator
    ``extra_args``, so configuration cannot weaken it; ``extra_args`` that
    would re-open a closed surface are rejected at worker startup.
    """

    name = "docker-airgapped"

    def __init__(
        self,
        image: str,
        term_grace: float = 10.0,
        memory: str = "512m",
        cpus: float = 1.0,
        pids_limit: int = 256,
        tmp_size: str = "64m",
        execution_timeout: float = 900,
        extra_args: list[str] | None = None,
    ):
        if not memory:
            raise ValueError(
                "[flux.workers] airgapped_memory must be non-empty: an "
                "unlimited-memory container defeats the airgapped profile",
            )
        if cpus <= 0:
            raise ValueError("[flux.workers] airgapped_cpus must be > 0")
        if pids_limit <= 0:
            raise ValueError("[flux.workers] airgapped_pids_limit must be > 0")
        for token in extra_args or []:
            flag = token.split("=", 1)[0]
            if flag in _AIRGAPPED_VETOED_FLAGS:
                raise ValueError(
                    f"[flux.workers] airgapped_extra_args contains '{token}': "
                    f"'{flag}' would weaken the airgapped isolation profile "
                    "and is not allowed",
                )
        super().__init__(
            image=image,
            term_grace=term_grace,
            # Base resource args stay empty: the profile owns the limits so
            # they land in _locked_args, after extra_args.
            network="",
            memory="",
            cpus=0.0,
            extra_args=extra_args,
            execution_timeout=execution_timeout,
        )
        self._airgapped_memory = memory
        self._airgapped_cpus = cpus
        self._pids_limit = pids_limit
        self._tmp_size = tmp_size

    def _locked_args(self) -> list[str]:
        return [
            "--network=none",
            "--read-only",
            "--tmpfs",
            f"/tmp:rw,size={self._tmp_size}",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit",
            str(self._pids_limit),
            "--memory",
            self._airgapped_memory,
            "--cpus",
            str(self._airgapped_cpus),
        ]
