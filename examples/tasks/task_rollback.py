from __future__ import annotations

from flux import task
from flux import workflow
from flux.tasks import choice


def rollback_for_bad_task(number):
    print(f"Rollback for task #{number}")


@task.with_options(rollback=rollback_for_bad_task)
def bad_task(number):
    should_fail = yield choice([True, False])
    if should_fail:
        print(f"Failed task #{number}")
        raise ValueError(f"Task #{number} failed")
    print(f"Succeed task #{number}")


@workflow
def task_rollback():
    yield bad_task(1)
    yield bad_task(2)
    yield bad_task(3)
    yield bad_task(4)


if __name__ == "__main__":  # pragma: no cover
    ctx = task_rollback.run()
    print(ctx.to_json())
