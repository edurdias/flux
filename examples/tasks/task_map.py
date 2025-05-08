from __future__ import annotations

from flux import task
from flux import workflow
from flux import WorkflowExecutionContext


@task
async def count(to: int):
    return [i for i in range(0, to + 1)]


@workflow
async def task_map(ctx: WorkflowExecutionContext[int]):
    results = await count.map(list(range(0, ctx.input)))
    return len(results)


if __name__ == "__main__":  # pragma: no cover
    ctx = task_map.run(10)
    print(ctx.to_json())
