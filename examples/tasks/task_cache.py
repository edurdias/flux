from __future__ import annotations

from flux import task
from flux import workflow
from flux import WorkflowExecutionContext


@task.with_options(cache=True)
async def multiply(a: int, b: int):
    return a * b


@workflow
async def workflow_with_cached_task(ctx: WorkflowExecutionContext[tuple[int, int, int]]):
    if ctx.input is None or len(ctx.input) != 3:
        raise ValueError("The input should be a tuple of three integers.")
    a, b, i = ctx.input
    results = []
    for _ in range(i):
        result = await multiply(a, b)
        results.append(result)
    return results


if __name__ == "__main__":  # pragma: no cover
    ctx = workflow_with_cached_task.run((2, 3, 3))
    print(ctx.to_json())
