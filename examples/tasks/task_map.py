from __future__ import annotations

from flux import ExecutionContext
from flux.task import task
from flux.workflow import workflow


@task
async def count(to: int):
    return [i for i in range(0, to + 1)]


@workflow
async def task_map(ctx: ExecutionContext[int]):
    results = await count.map(list(range(0, ctx.input)))
    return len(results)


if __name__ == "__main__":  # pragma: no cover
    ctx = task_map.run(10)
    print(ctx.to_json())
