from __future__ import annotations

from flux import task
from flux import workflow
from flux.context import WorkflowExecutionContext


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
async def nested_tasks_workflow(ctx: WorkflowExecutionContext):
    await first_task()
    await second_task()
    await three_levels_task()


if __name__ == "__main__":  # pragma: no cover
    ctx = nested_tasks_workflow.run()
    print(ctx.to_json())
