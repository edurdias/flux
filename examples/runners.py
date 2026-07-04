"""Pinning workflows to a specific execution runner.

Workers run each execution through a *runner*. Which one is used comes from
the worker's ``[flux.workers] default_runner`` (``subprocess`` unless
changed) — or from the workflow itself via
``@workflow.with_options(runner=...)``, which also constrains dispatch: the
workflow only goes to workers that advertise that runner.

- ``subprocess`` (the default): one child process per execution. Fault
  isolation — a crash or blocking call can't take down the worker — and a
  sanitized environment: workflow code never sees worker credentials.
- ``inprocess``: runs on the worker's event loop. Lowest latency, no
  isolation; pair it with trusted, async-clean workflows — transient
  mesh hops especially.
- ``docker``: one container per execution (workers must enable it and set
  ``docker_image``). Full filesystem/dependency isolation for untrusted
  code.

The runner option matters when executions are dispatched to workers;
running a workflow inline (``workflow.run()``, as below) executes in the
current process regardless.
"""

from __future__ import annotations

from flux import ExecutionContext
from flux.task import task
from flux.workflow import workflow


@task
async def double(x: int) -> int:
    return x * 2


@workflow.with_options(runner="inprocess")
async def fast_hop(ctx: ExecutionContext[int]):
    """Latency-sensitive and trusted: stay on the worker's event loop.

    Combine with ``durability="transient"`` for the lowest-overhead
    configuration for agent-to-agent (mesh) calls.
    """
    if ctx.input is None:
        raise TypeError("Input not provided")
    return await double(ctx.input)


@workflow.with_options(runner="subprocess")
async def isolated_by_default(ctx: ExecutionContext[int]):
    """Explicitly pinned to the subprocess runner.

    This is also the worker default, so the annotation here just makes the
    requirement dispatch-enforced: the workflow will only run on workers
    that advertise the subprocess runner.
    """
    if ctx.input is None:
        raise TypeError("Input not provided")
    return await double(ctx.input)


@workflow.with_options(runner="docker")
async def containerized(ctx: ExecutionContext[int]):
    """Runs in its own container, on workers configured with:

        [flux.workers]
        runners = ["inprocess", "subprocess", "docker"]
        docker_image = "<registry>/flux:<version-matching-the-worker>"

    Use for untrusted code or conflicting dependency sets. Workers without
    the docker runner never receive this workflow.
    """
    if ctx.input is None:
        raise TypeError("Input not provided")
    return await double(ctx.input)


if __name__ == "__main__":  # pragma: no cover
    ctx = fast_hop.run(21)
    print(ctx.to_json())
    ctx = isolated_by_default.run(21)
    print(ctx.to_json())
    ctx = containerized.run(21)
    print(ctx.to_json())
