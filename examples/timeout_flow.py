import time

from flux import task, workflow


@task()
def quick_task():
    print("Quick task")


@workflow(timeout=6)
def timeout():
    yield quick_task()
    time.sleep(5)


if __name__ == "__main__":
    ctx = timeout.run()
    print(ctx.to_json())
