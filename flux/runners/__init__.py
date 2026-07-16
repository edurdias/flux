"""Pluggable execution runners.

A runner decides *where* a claimed workflow executes; the worker keeps
owning everything server-facing (claims, the checkpoint outbox, auth,
heartbeats, progress delivery). ``InProcessRunner`` runs the workflow as an
asyncio task in the worker process — the lowest-latency path. The default
``SubprocessRunner`` runs each execution in its own child process, so a
crash, OOM, or blocking call cannot take down the worker or its co-resident
executions, and cancellation can always be enforced with a signal.

The interface deliberately assumes neither stdio nor same-host execution, so
container-based runners (Docker, Kubernetes) fit behind it later.
"""

from __future__ import annotations

from flux.runners.base import Runner, RunnerHooks
from flux.runners.inprocess import InProcessRunner
from flux.runners.loader import WorkflowModuleLoader
from flux.runners.subprocess_runner import SubprocessRunner

KNOWN_RUNNERS = ("inprocess", "subprocess", "docker", "docker-airgapped")


def create_runners(names: list[str], config) -> dict[str, Runner]:
    """Instantiate the enabled runners from the worker's config section.

    Unknown names raise at worker startup — a misconfigured fleet should fail
    fast, not at first dispatch.
    """
    runners: dict[str, Runner] = {}
    for name in names:
        if name == "inprocess":
            runners[name] = InProcessRunner(
                loader=WorkflowModuleLoader(
                    ttl=config.module_cache_ttl,
                    max_size=config.module_cache_max_size,
                ),
            )
        elif name == "subprocess":
            runners[name] = SubprocessRunner(
                term_grace=config.subprocess_term_grace,
                memory_limit=config.subprocess_memory_limit,
            )
        elif name == "docker":
            from flux.runners.docker import DockerRunner

            runners[name] = DockerRunner(
                image=config.docker_image,
                term_grace=config.subprocess_term_grace,
                network=config.docker_network,
                memory=config.docker_memory,
                cpus=config.docker_cpus,
                extra_args=list(config.docker_extra_args),
            )
        elif name == "docker-airgapped":
            from flux.runners.docker import AirgappedDockerRunner

            image = config.airgapped_image or config.docker_image
            if not image:
                raise ValueError(
                    "[flux.workers] airgapped_image (or docker_image) must be "
                    "set when the 'docker-airgapped' runner is enabled",
                )
            runners[name] = AirgappedDockerRunner(
                image=image,
                term_grace=config.subprocess_term_grace,
                memory=config.airgapped_memory,
                cpus=config.airgapped_cpus,
                pids_limit=config.airgapped_pids_limit,
                tmp_size=config.airgapped_tmp_size,
                execution_timeout=config.airgapped_execution_timeout,
                extra_args=list(config.airgapped_extra_args),
                gpus=config.airgapped_gpus,
                mounts=list(config.airgapped_mounts),
                shm_size=config.airgapped_shm_size,
                service_sockets=dict(config.airgapped_service_sockets),
            )
        else:
            raise ValueError(
                f"Unknown runner '{name}' in [flux.workers] runners; "
                f"known runners: {', '.join(KNOWN_RUNNERS)}",
            )
    return runners


__all__ = [
    "KNOWN_RUNNERS",
    "InProcessRunner",
    "Runner",
    "RunnerHooks",
    "SubprocessRunner",
    "WorkflowModuleLoader",
    "create_runners",
]
