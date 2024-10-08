import time

from flux import task, workflow


@task()
def quick_task():
    print("Quick task")


@workflow(timeout=6)
def flow_timeout():
    yield quick_task()
    time.sleep(10)


if __name__ == "__main__":
    ctx = flow_timeout.run()
    print(ctx.to_json())
