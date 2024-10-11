import random

from flux import workflow, task


@task.with_options(retry_max_attemps=10, retry_delay=2)
def bad_task(number):
    if random.random() < 0.7:
        print(f"Failed task #{number}")
        raise ValueError()
    print(f"Succeed task #{number}")


@workflow
def retries():
    yield bad_task(1)
    yield bad_task(2)


if __name__ == "__main__":
    ctx = retries.run()
    print(ctx.to_json())
