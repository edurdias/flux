from __future__ import annotations

from flux import task
from flux import workflow

counter = 1


@task.with_options(retry_max_attemps=3, retry_delay=1)
def bad_task(number):
    global counter
    if counter < 3:
        print(f"Failed task #{number}")
        counter += 1
        raise ValueError()
    print(f"Succeed task #{number}")


@workflow
def task_retries():
    yield bad_task(1)
    yield bad_task(2)


if __name__ == "__main__":  # pragma: no cover
    ctx = task_retries.run()
    print(ctx.to_json())
