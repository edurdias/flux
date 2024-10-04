import json
import random

from flux import task, workflow
from flux.encoders import WorkflowContextEncoder
from flux.context import WorkflowExecutionContext


@task(retry_max_attemps=10, retry_delay=2)
def bad_task(number):
    if random.random() < 0.7:
        print(f"Failed task #{number}")
        raise ValueError()
    print(f"Succeed task #{number}")


@workflow
def retries(ctx: WorkflowExecutionContext[str]):
    yield bad_task(1)
    yield bad_task(2)


if __name__ == "__main__":
    ctx = retries.run()
    print(ctx.output)
    print(json.dumps(ctx, indent=4, cls=WorkflowContextEncoder))
