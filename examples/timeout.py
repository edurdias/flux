import time

from flux import task, workflow


@task(timeout=3)
def long_task():
    time.sleep(10)


@workflow
def timeout():
    yield long_task()


if __name__ == "__main__":
    ctx = timeout.run()
    print(ctx.to_json())
