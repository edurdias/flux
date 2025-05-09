from __future__ import annotations

from flux import task
from flux import workflow
from flux import WorkflowExecutionContext
from flux.tasks import pipeline


@task
async def multiply_by_two(x):
    return x * 2


@task
async def add_three(x):
    return x + 3


@task
async def square(x):
    return x * x


@workflow
async def simple_pipeline(ctx: WorkflowExecutionContext[int]):
    if not ctx.input:
        raise TypeError("Input not provided")
    result = await pipeline(multiply_by_two, add_three, square, input=ctx.input)
    return result


if __name__ == "__main__":  # pragma: no cover
    ctx = simple_pipeline.run(5)
    print(ctx.to_json())
