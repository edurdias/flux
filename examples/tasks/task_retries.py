from __future__ import annotations

import random

from flux import task
from flux import workflow


@task.with_options(retry_max_attemps=10, retry_delay=2)
def bad_task(number):
    if random.random() < 0.7:
        print(f"Failed task #{number}")
        raise ValueError()
    print(f"Succeed task #{number}")


@workflow
def task_retries():
    yield bad_task(1)
    yield bad_task(2)


if __name__ == "__main__":  # pragma: no cover
    ctx = task_retries.run()
    print(ctx.to_json())