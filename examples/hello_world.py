import json

from flux import task, workflow
from flux.encoders import WorkflowContextEncoder
from flux.context import WorkflowExecutionContext


@task
def say_hello(name: str):
    return f"Hello, {name}"


@workflow
def hello_world(ctx: WorkflowExecutionContext[str]):
    return (yield say_hello(ctx.input))


if __name__ == "__main__":
    ctx = hello_world.run("Joe")
    print(ctx.output)
    print(json.dumps(ctx, indent=4, cls=WorkflowContextEncoder))
