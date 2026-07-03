from __future__ import annotations

from flux import ExecutionContext
from flux.task import task
from flux.workflow import workflow


@task
async def double(n: int):
    return n * 2


@workflow.with_options(durability="transient")
async def transient_double(ctx: ExecutionContext[int]):
    a = await double(ctx.input)
    return await double(a)


@workflow.with_options(durability="transient")
async def transient_pause_attempt(ctx: ExecutionContext[str]):
    from flux.tasks import pause

    await pause("never-allowed")
    return "unreachable"
