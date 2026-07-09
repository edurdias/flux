"""Fixtures for the dynamic-routing (scoring policy) e2e scenarios."""

from __future__ import annotations

from flux import ExecutionContext, task, workflow
from flux.routing import input, label, least, load, metric, most, prefer, score


@task
async def ident(value):
    return value


@workflow.with_options(
    routing=score(
        prefer(label("pin") == input("pin"), weight=10),
        least(load()),
    ),
)
async def pin_router(ctx: ExecutionContext[dict]):
    """Payload-driven locality: lands on the worker whose 'pin' label
    matches the execution input's 'pin' field."""
    return await ident(ctx.input)


@workflow.with_options(routing=score(most(metric("fitness"), weight=10)))
async def fitness_router(ctx: ExecutionContext):
    """Metric-driven placement: lands on the worker advertising the highest
    'fitness' value from its metrics provider."""
    return await ident(ctx.input)


@workflow.with_options(routing=score(most(metric("flux.running_executions"), weight=10)))
async def busy_router(ctx: ExecutionContext):
    """Built-in-metric placement, deliberately anti-least-loaded: prefers the
    BUSY worker, so landing there proves the flux.* metric drove the choice
    (the default selection would pick the idle one)."""
    return await ident(ctx.input)


@workflow.with_options(affinity={"pin": "target"})
async def slow_occupant(ctx: ExecutionContext):
    """Occupies the pin=target worker long enough for its heartbeat to
    advertise flux.running_executions >= 1. Distinct sleep durations keep
    deterministic task ids from replaying instead of sleeping."""
    from flux.tasks import sleep

    for i in range(45):
        await sleep(1 + i / 1000)
    return "done"
