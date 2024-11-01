from __future__ import annotations

from flux import task
from flux import workflow


@task
def third_task():
    return "result"


@task
def first_task():
    result = yield third_task()
    return result


@task
def second_task():
    second = yield third_task()
    return [second, "third"]


@task
def three_levels_task():
    result = yield second_task()
    return ["three_levels", *result]


@workflow
def nested_tasks_workflow():
    yield first_task()
    yield second_task()
    yield three_levels_task()


if __name__ == "__main__":  # pragma: no cover
    ctx = nested_tasks_workflow.run()
    print(ctx.to_json())
