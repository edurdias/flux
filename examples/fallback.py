from __future__ import annotations

import random

from flux import task
from flux import workflow


def fallback_for_bad_task(number):
    print(f"Fallback for task #{number}")


@task.with_options(fallback=fallback_for_bad_task)
def bad_task(number):
    if random.choice([True, False]):
        print(f"Failed task #{number}")
        raise ValueError()
    print(f"Succeed task #{number}")


@workflow
def fallback():
    yield bad_task(1)
    yield bad_task(2)
    yield bad_task(3)
    yield bad_task(4)


if __name__ == "__main__":  # pragma: no cover
    ctx = fallback.run()
    print(ctx.to_json())
