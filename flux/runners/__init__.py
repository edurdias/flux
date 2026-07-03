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

KNOWN_RUNNERS = ("inprocess", "subprocess")


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
