import asyncio

from flux import task, workflow


@task
async def slow_task(iterations: int):
    for i in range(iterations):
        await asyncio.sleep(2)
    return f"completed {iterations} iterations"


@workflow
async def cancellable_e2e(ctx):
    return await slow_task(ctx.input or 10)
