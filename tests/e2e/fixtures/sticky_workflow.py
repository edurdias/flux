"""Fixtures for the sticky-routing (X-Flux-Preferred-Worker) e2e scenarios."""

from __future__ import annotations

from flux import ExecutionContext
from flux.task import task
from flux.tasks import call
from flux.workflow import workflow


@task
async def add_one(n: int):
    return n + 1


@workflow.with_options(durability="transient")
async def sticky_child(ctx: ExecutionContext[int]):
    return await add_one(ctx.input)


@workflow
async def sticky_parent(ctx: ExecutionContext[int]):
    """Relay via string ref: always dispatches a child execution; the relay
    carries the sticky hint so the child lands on this worker."""
    return await call("sticky_child", ctx.input)


@workflow.with_options(durability="transient", affinity={"pin": "target"})
async def pinned_child(ctx: ExecutionContext[int]):
    return await add_one(ctx.input)


@workflow.with_options(affinity={"pin": "parent"})
async def pinned_parent(ctx: ExecutionContext[int]):
    """Runs on the pin=parent worker; the child's affinity excludes that
    worker, so the sticky hint is ineligible and dispatch must fall back."""
    return await call("pinned_child", ctx.input)
