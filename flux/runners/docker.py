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

Container CLI compatibility
---------------------------

The airgapped runner can drive rootless Podman or nerdctl instead of the
docker CLI via ``[flux.workers] airgapped_container_cli`` (a named config
key, like every other airgapped grant, so the config file stays the audit
trail). The runner relies on the following argv-compatible surface, which
all three CLIs implement with docker semantics:

- ``run -i --rm --name`` with implicit signal proxying (sig-proxy is the
  default without a TTY in docker, podman, and nerdctl — SIGTERM reaches
  the child for graceful cancellation)
- ``kill <container>`` for force-kill
- resource/limit flags: ``--network`` / ``--network=none``, ``--memory``,
  ``--cpus``, ``--pids-limit``, ``--read-only``, ``--tmpfs``,
  ``--cap-drop=ALL``, ``--security-opt=no-new-privileges``
- grants: ``--mount type=bind,source=..,target=..[,readonly]``,
  ``--gpus`` (podman >= 4.7 / nerdctl require working CDI or the
  nvidia toolkit, same as docker), ``--shm-size``, ``--env``

Anything outside this set (added via ``airgapped_extra_args``) is the
operator's responsibility to keep compatible with the chosen CLI.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import stat
import subprocess
from typing import TYPE_CHECKING
from uuid import uuid4

from flux.runners.subprocess_runner import _STREAM_LIMIT, SubprocessRunner
from flux.utils import get_logger

if TYPE_CHECKING:
    from flux.worker import WorkflowExecutionRequest

logger = get_logger(__name__)

# CLIs the runners know how to drive. The emitted-flag compatibility set is
# documented in the module docstring; a name outside this tuple fails at
# worker startup, not at first dispatch.
SUPPORTED_CONTAINER_CLIS = ("docker", "podman", "nerdctl")


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
        cli: str = "docker",
    ):
        super().__init__(term_grace=term_grace, execution_timeout=execution_timeout)
        if not image:
            raise ValueError(
                "[flux.workers] docker_image must be set when the 'docker' runner is enabled",
            )
        if cli not in SUPPORTED_CONTAINER_CLIS:
            raise ValueError(
                f"[flux.workers] airgapped_container_cli '{cli}' is not "
                f"supported; choose one of: {', '.join(SUPPORTED_CONTAINER_CLIS)}",
            )
        self._image = image
        self._cli = cli
        self._network = network
        self._memory = memory
        self._cpus = cpus
        self._extra_args = list(extra_args or [])
        # container-CLI pid -> container name, for '<cli> kill' on force kill.
        self._containers: dict[int, str] = {}
        self._verify_cli_available()

    def _verify_cli_available(self):
        """Fail at worker startup, not at first dispatch."""
        if shutil.which(self._cli) is None:
            raise ValueError(
                f"The '{self.name}' runner is enabled but the {self._cli} CLI "
                "is not on PATH",
            )
        if self._cli == "docker":
            # docker requires a reachable daemon; probe it explicitly so the
            # error names the actual problem instead of failing per-dispatch.
            probe = subprocess.run(
                ["docker", "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if probe.returncode != 0:
                raise ValueError(
                    f"The '{self.name}' runner is enabled but the Docker "
                    "daemon is unreachable: "
                    f"{probe.stderr.strip() or probe.stdout.strip()}",
                )
            logger.info(f"{self.name} runner ready (docker server {probe.stdout.strip()})")
        else:
            # podman is daemonless; nerdctl needs containerd and reports it
            # through a non-zero 'version' exit. A plain version probe covers
            # both without depending on CLI-specific template keys.
            probe = subprocess.run(
                [self._cli, "version"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if probe.returncode != 0:
                raise ValueError(
                    f"The '{self.name}' runner is enabled but '{self._cli} "
                    "version' failed — the container runtime is not usable: "
                    f"{probe.stderr.strip() or probe.stdout.strip()}",
                )
            logger.info(f"{self.name} runner ready (cli {self._cli})")

    def _container_name(self, execution_id: str) -> str:
        # Unique per attempt: a crashed execution can be re-dispatched to this
        # worker while its previous --rm container is still being removed.
        return f"flux-exec-{execution_id[:24]}-{uuid4().hex[:6]}"

    def _build_command(self, container_name: str) -> list[str]:
        """Template method. Sections in order: fixed prefix, config-derived
        resource args, operator extra_args, then ``_locked_args()`` — LAST so
        docker's last-wins flag parsing structurally favors a hardened
        profile over anything an operator (or config-file attacker) adds."""
        command = [self._cli, "run", "-i", "--rm", "--name", container_name]
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
        # Killing the container CLI alone would orphan the container; kill
        # the container (which also ends the attached CLI process).
        container_name = self._containers.get(proc.pid)
        if container_name:
            killer = await asyncio.create_subprocess_exec(
                self._cli,
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
# can't be enumerated, while the guarantees to protect can. Capabilities that
# ARE grantable (GPUs, read-only mounts, shared memory) are vetoed here too:
# each one is grantable only through its named airgapped_* config key, so the
# config file is the complete audit trail of opened surfaces.
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
        "--gpus",
        "--shm-size",
    },
)


SERVICE_SOCKET_FILENAME = "service.sock"
SERVICE_MOUNT_PREFIX = "/run/flux/services"


def _validate_service_sockets(services: dict[str, str]) -> dict[str, str]:
    """Validate the ``airgapped_service_sockets`` map (name -> host dir).

    The directory contract: mode ``0555`` (no write bits at all), socket
    ``service.sock`` inside at ``0666``. The mount must be rw — connecting
    to a UDS requires write permission on the socket inode — but the
    profile's ``--cap-drop=ALL`` strips ``CAP_DAC_OVERRIDE``, so the
    write-less directory mode binds every container uid, root included:
    executions can connect to the sockets, never create files (no
    dead-drop between executions). Host-side root (the sidecar or its unit
    manager) keeps ``CAP_DAC_OVERRIDE`` and can still create or replace
    the socket across restarts.
    """
    # Same rule flux.routing.service() validates workflow-side, so an
    # expression can never target a name registration could not grant.
    from flux.routing import is_valid_service_name

    validated: dict[str, str] = {}
    seen_dirs: set[str] = set()
    for name, host_dir in services.items():
        if not is_valid_service_name(name):
            raise ValueError(
                f"[flux.workers] airgapped_service_sockets name '{name}' is "
                "invalid: use lowercase letters, digits, and single hyphens "
                "(max 32 chars) — it becomes the worker label "
                f"'flux.service.{name}'",
            )
        if not os.path.isabs(host_dir):
            raise ValueError(
                f"[flux.workers] airgapped_service_sockets['{name}']: host "
                f"directory '{host_dir}' must be an absolute path",
            )
        if "," in host_dir:
            # --mount options are comma-separated; a comma in the path
            # would silently change the mount spec.
            raise ValueError(
                f"[flux.workers] airgapped_service_sockets['{name}']: host "
                "directory must not contain commas",
            )
        host_dir = os.path.normpath(host_dir)
        if host_dir in seen_dirs:
            raise ValueError(
                f"[flux.workers] airgapped_service_sockets['{name}']: host "
                f"directory '{host_dir}' is already used by another service",
            )
        if not os.path.exists(host_dir):
            try:
                os.makedirs(host_dir)
                os.chmod(host_dir, 0o555)
            except OSError as e:
                raise ValueError(
                    f"[flux.workers] airgapped_service_sockets['{name}']: "
                    f"could not create '{host_dir}' ({e}); create it "
                    "manually with mode 0555",
                ) from e
        elif not os.path.isdir(host_dir):
            raise ValueError(
                f"[flux.workers] airgapped_service_sockets['{name}']: "
                f"'{host_dir}' exists but is not a directory",
            )
        else:
            mode = os.stat(host_dir).st_mode & 0o777
            if mode & 0o222:
                raise ValueError(
                    f"[flux.workers] airgapped_service_sockets['{name}']: "
                    f"'{host_dir}' has mode {mode:o}; the directory must "
                    "carry no write bits (chmod 0555) so sealed executions "
                    "cannot use it as a shared writable surface",
                )
        socket_path = os.path.join(host_dir, SERVICE_SOCKET_FILENAME)
        if not os.path.exists(socket_path):
            logger.warning(
                f"Service '{name}': no socket at {socket_path} yet — the "
                "sidecar may not be running; executions using this service "
                "will fail to connect until it is",
            )
        else:
            socket_stat = os.stat(socket_path)
            if not stat.S_ISSOCK(socket_stat.st_mode):
                logger.warning(
                    f"Service '{name}': {socket_path} exists but is not a "
                    "unix socket; executions will fail to connect to it",
                )
            elif socket_stat.st_mode & 0o002 == 0:
                logger.warning(
                    f"Service '{name}': {socket_path} is not "
                    "world-connectable (mode "
                    f"{socket_stat.st_mode & 0o777:o}, expected 0666); "
                    "container uids without write permission on the socket "
                    "will fail to connect",
                )
        seen_dirs.add(host_dir)
        validated[name] = host_dir
    return validated


def _parse_airgapped_mounts(mounts: list[str]) -> list[tuple[str, str]]:
    """Validate ``airgapped_mounts`` entries into (source, target) pairs.

    Entry format is ``/host/path:/container/path`` with an optional,
    redundant ``:ro`` suffix. Read-only is forced by the runner regardless;
    anything else after the target (``rw`` above all) is rejected.
    """
    parsed: list[tuple[str, str]] = []
    seen_targets: set[str] = set()
    for entry in mounts:
        parts = entry.split(":")
        if len(parts) == 3 and parts[2] == "ro":
            parts = parts[:2]
        if len(parts) != 2:
            raise ValueError(
                f"[flux.workers] airgapped_mounts entry '{entry}' is invalid: "
                "expected '/host/path:/container/path' (optionally ':ro'; "
                "mounts are always read-only)",
            )
        source, target = parts
        if not (os.path.isabs(source) and os.path.isabs(target)):
            raise ValueError(
                f"[flux.workers] airgapped_mounts entry '{entry}': both the "
                "host and container paths must be absolute",
            )
        if "," in source or "," in target:
            # --mount options are comma-separated; a comma in a path would
            # silently change the mount spec.
            raise ValueError(
                f"[flux.workers] airgapped_mounts entry '{entry}': paths must not contain commas",
            )
        # Normalize before the guards below: '/.', '/models/..' and trailing
        # slashes would otherwise slip past the root-target and duplicate
        # checks while resolving to the same location in the container.
        source = os.path.normpath(source)
        target = os.path.normpath(target)
        # POSIX normpath preserves '//', so test all-slash rather than '/'.
        if target.rstrip("/") == "":
            raise ValueError(
                f"[flux.workers] airgapped_mounts entry '{entry}': mounting "
                "over '/' is not allowed",
            )
        if target in seen_targets:
            raise ValueError(
                f"[flux.workers] airgapped_mounts entry '{entry}': duplicate "
                f"container target '{target}'",
            )
        if not os.path.exists(source):
            raise ValueError(
                f"[flux.workers] airgapped_mounts entry '{entry}': host path "
                f"'{source}' does not exist",
            )
        seen_targets.add(target)
        parsed.append((source, target))
    return parsed


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

    Capabilities are grantable — each only through its named config key,
    never through ``extra_args``, so the config file is the audit trail:
    ``airgapped_gpus`` (compute device, no data path out),
    ``airgapped_mounts`` (bind mounts with read-only forced by the runner —
    an input channel for reference data and static assets),
    ``airgapped_shm_size`` (``/dev/shm`` sizing for workloads that pass
    large buffers between processes), and ``airgapped_service_sockets``
    (Unix-socket access to long-lived, operator-managed sidecars on the
    worker host — warm runtimes consumed point-to-point, with no network
    stack anywhere; see ``_validate_service_sockets`` for the directory
    contract). Socket traffic is the one channel the worker does not
    mediate; everything else still leaves only through the stdio protocol.

    The container CLI itself is configurable through the same named-key
    shape (``airgapped_container_cli``: ``docker`` | ``podman`` |
    ``nerdctl``) so rootless deployments can drive Podman or nerdctl
    instead of a privileged Docker daemon; the emitted-flag compatibility
    set is documented in the module docstring, and the chosen CLI is
    validated at worker startup.
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
        gpus: str = "",
        mounts: list[str] | None = None,
        shm_size: str = "",
        service_sockets: dict[str, str] | None = None,
        container_cli: str = "docker",
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
                hint = ""
                if flag == "--gpus":
                    hint = " (grant GPUs via [flux.workers] airgapped_gpus)"
                elif flag == "--shm-size":
                    hint = " (size /dev/shm via [flux.workers] airgapped_shm_size)"
                elif flag in ("--volume", "-v", "--mount"):
                    hint = " (read-only mounts go through [flux.workers] airgapped_mounts)"
                raise ValueError(
                    f"[flux.workers] airgapped_extra_args contains '{token}': "
                    f"'{flag}' would weaken the airgapped isolation profile "
                    f"and is not allowed{hint}",
                )
        self._mounts = _parse_airgapped_mounts(list(mounts or []))
        self._gpus = gpus
        self._shm_size = shm_size
        self._service_sockets = _validate_service_sockets(dict(service_sockets or {}))
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
            cli=container_cli,
        )
        self._airgapped_memory = memory
        self._airgapped_cpus = cpus
        self._pids_limit = pids_limit
        self._tmp_size = tmp_size

    @property
    def service_names(self) -> list[str]:
        """Granted service-socket names; advertised as worker labels."""
        return sorted(self._service_sockets)

    def _locked_args(self) -> list[str]:
        args = [
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
        # Named capability knobs. Emitted here — not accepted in extra_args —
        # so each grant is explicit config; read-only is forced on mounts no
        # matter what the entry said.
        for source, target in self._mounts:
            args += ["--mount", f"type=bind,source={source},target={target},readonly"]
        if self._gpus:
            args += ["--gpus", self._gpus]
        if self._shm_size:
            args += ["--shm-size", self._shm_size]
        # Service sockets: the one deliberately-rw mount family (UDS connect
        # needs write on the socket inode); the 0555 directory contract plus
        # --cap-drop=ALL keeps it write-proof for every container uid. The
        # env var is emitted here, after extra_args, so docker's last-wins
        # parsing keeps the advertised map authoritative.
        for name in sorted(self._service_sockets):
            args += [
                "--mount",
                f"type=bind,source={self._service_sockets[name]},"
                f"target={SERVICE_MOUNT_PREFIX}/{name}",
            ]
        if self._service_sockets:
            socket_map = {
                name: f"{SERVICE_MOUNT_PREFIX}/{name}/{SERVICE_SOCKET_FILENAME}"
                for name in self._service_sockets
            }
            args += [
                "--env",
                f"FLUX_SERVICE_SOCKETS={json.dumps(socket_map, sort_keys=True, separators=(',', ':'))}",
            ]
        return args
