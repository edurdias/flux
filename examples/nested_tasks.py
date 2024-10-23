from __future__ import annotations

from flux import task
from flux import workflow


@task
def third_task():
    return "result"


@task
def first_task():
    return (yield third_task())


@task
def second_task():
    second = yield third_task()
    return [second, "third"]


@task
def three_levels_task():
    return (yield second_task())


@workflow
def nested_tasks():
    yield first_task()
    yield second_task()
    yield three_levels_task()


if __name__ == "__main__":  # pragma: no cover
    ctx = nested_tasks.run()
    print(ctx.to_json())
