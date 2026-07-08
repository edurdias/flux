"""Fixtures for the dynamic-routing (scoring policy) e2e scenarios."""

from __future__ import annotations

from flux import ExecutionContext, task, workflow
from flux.routing import input, least, most, prefer, score


@task
async def ident(value):
    return value


@workflow.with_options(
    routing=score(
        prefer("label:pin", "==", input("pin"), weight=10),
        least("load"),
    ),
)
async def pin_router(ctx: ExecutionContext[dict]):
    """Payload-driven locality: lands on the worker whose 'pin' label
    matches the execution input's 'pin' field."""
    return await ident(ctx.input)


@workflow.with_options(routing=score(most("metric:fitness", weight=10)))
async def fitness_router(ctx: ExecutionContext):
    """Metric-driven placement: lands on the worker advertising the highest
    'fitness' value from its metrics provider."""
    return await ident(ctx.input)
