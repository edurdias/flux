import json
import random

from flux import activity, workflow
from flux.encoders import WorkflowContextEncoder
from flux.loaders import LocalFunctionWorkflowLoader
from flux.context import WorkflowExecutionContext
from flux.runners import LocalWorkflowRunner


@activity(retry_max_attemps=10, retry_delay=2)
def bad_activity(number):
    if random.random() < 0.7:
        print(f"Failed activity #{number}")
        raise ValueError()
    print(f"Succeed activity #{number}")


@workflow
def retries(ctx: WorkflowExecutionContext[str]):
    yield bad_activity(1)
    yield bad_activity(2)


if __name__ == "__main__":
    runner = LocalWorkflowRunner(LocalFunctionWorkflowLoader(globals()))
    ctx = runner.run_workflow("retries")
    print(ctx.output)
    print(json.dumps(ctx, indent=4, cls=WorkflowContextEncoder))
