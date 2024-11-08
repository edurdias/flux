from __future__ import annotations

from flux import task
from flux import workflow
from flux import WorkflowExecutionContext


@task.with_options(cache=True)
def multiply(a: int, b: int):
    return a * b


@workflow
def workflow_with_cached_task(ctx: WorkflowExecutionContext[tuple[int, int, int]]):
    a, b, i = ctx.input
    results = []
    for _ in range(i):
        result = yield multiply(a, b)
        results.append(result)
    return results


if __name__ == "__main__":  # pragma: no cover
    ctx = workflow_with_cached_task.run((2, 3, 3))
    print(ctx.to_json())
