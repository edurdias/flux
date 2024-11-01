from __future__ import annotations

from flux import task
from flux import workflow


def fallback_for_bad_task(number: int, should_fail: bool = True):
    print(f"Fallback for task #{number}")


@task.with_options(fallback=fallback_for_bad_task)
def bad_task(number: int, should_fail: bool = True):
    if should_fail:
        print(f"Failed task #{number}")
        raise ValueError()
    print(f"Succeed task #{number}")


@workflow
def task_fallback():
    yield bad_task(1)
    yield bad_task(2, False)
    yield bad_task(3)
    yield bad_task(4, False)


if __name__ == "__main__":  # pragma: no cover
    ctx = task_fallback.run()
    print(ctx.to_json())
