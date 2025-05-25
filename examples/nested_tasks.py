from __future__ import annotations

from flux import ExecutionContext
from flux.task import task
from flux.workflow import workflow


@task
async def third_task():
    return "result"


@task
async def first_task():
    result = await third_task()
    return result


@task
async def second_task():
    second = await third_task()
    return [second, "third"]


@task
async def three_levels_task():
    result = await second_task()
    return ["three_levels", *result]


@workflow
async def nested_tasks_workflow(ctx: ExecutionContext):
    await first_task()
    await second_task()
    await three_levels_task()


if __name__ == "__main__":  # pragma: no cover
    ctx = nested_tasks_workflow.run()
    print(ctx.to_json())
