from __future__ import annotations

from flux import ExecutionContext
from flux.task import task
from flux.workflow import workflow


async def fallback_for_bad_task(number: int, should_fail: bool = True):
    print(f"Fallback for task #{number}")
    return f"fallback for bad_task #{number} succeed"


@task.with_options(fallback=fallback_for_bad_task, retry_max_attempts=2)
async def bad_task(number: int, should_fail: bool = True):
    if should_fail:
        print(f"Failed task #{number}")
        raise ValueError()
    print(f"Succeed task #{number}")
    return f"bad_task #{number} succeed"


@workflow
async def task_fallback_after_retry(ctx: ExecutionContext):
    result1 = await bad_task(1)
    result2 = await bad_task(2, False)  # will pass
    return [result1, result2]


if __name__ == "__main__":  # pragma: no cover
    ctx = task_fallback_after_retry.run()
    print(ctx.to_json())
