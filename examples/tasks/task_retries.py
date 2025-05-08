from __future__ import annotations

from flux import task
from flux import workflow
from flux.context import WorkflowExecutionContext

counter = 1


@task.with_options(retry_max_attempts=3, retry_delay=1)
async def bad_task(number):
    global counter
    if counter < 3:
        print(f"Failed task #{number}")
        counter += 1
        raise ValueError()
    print(f"Succeed task #{number}")


@workflow
async def task_retries(ctx: WorkflowExecutionContext):
    await bad_task(1)
    await bad_task(2)


if __name__ == "__main__":  # pragma: no cover
    ctx = task_retries.run()
    print(ctx.to_json())
