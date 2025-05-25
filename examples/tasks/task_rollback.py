from __future__ import annotations

from flux import ExecutionContext
from flux.task import task
from flux.workflow import workflow


async def rollback_for_bad_task(number: int, should_fail: bool = True):
    print(f"Rollback for task #{number}")


@task.with_options(rollback=rollback_for_bad_task)
async def bad_task(number: int, should_fail: bool = True):
    if should_fail:
        print(f"Failed task #{number}")
        raise ValueError()
    print(f"Succeed task #{number}")


@workflow
async def task_rollback(ctx: ExecutionContext):
    await bad_task(1, False)
    await bad_task(2, False)
    await bad_task(3)


if __name__ == "__main__":  # pragma: no cover
    ctx = task_rollback.run()
    print(ctx.to_json())
