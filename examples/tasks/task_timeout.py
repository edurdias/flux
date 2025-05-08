from __future__ import annotations

import asyncio

from flux import task
from flux import workflow
from flux.context import WorkflowExecutionContext


@task.with_options(timeout=1)
async def long_task():
    await asyncio.sleep(10)


@task
async def nested_task():
    await long_task()


@workflow
async def task_timeout(ctx: WorkflowExecutionContext):
    await long_task()


@workflow
async def task_nested_timeout(ctx: WorkflowExecutionContext):
    await nested_task()


if __name__ == "__main__":  # pragma: no cover
    ctx = task_timeout.run()
    print(ctx.to_json())

    ctx = task_nested_timeout.run()
    print(ctx.to_json())
