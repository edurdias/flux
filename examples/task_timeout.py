import time

from flux import task, workflow


@task(timeout=3)
def long_task():
    time.sleep(5)


@task
def nested_task():
    yield long_task()


@workflow
def task_timeout():
    yield long_task()


@workflow
def task_nested_timeout():
    yield nested_task()


if __name__ == "__main__":
    ctx = task_timeout.run()
    print(ctx.to_json())

    ctx = task_nested_timeout.run()
    print(ctx.to_json())
