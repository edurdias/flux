import json

from flux.activity import activity
from flux.loaders import LocalFunctionWorkflowLoader
from flux.workflow import workflow
from flux.context import WorkflowExecutionContext
from flux.encoders import WorkflowContextEncoder
from flux.runners import LocalWorkflowRunner


@activity
def say_hello(name: str):
    return f"Hello, {name}"


@workflow
def hello_world(ctx: WorkflowExecutionContext[str]):
    hello_you = yield say_hello(ctx.input)
    hello_world = yield say_hello("World")
    return [hello_you, hello_world]


if __name__ == "__main__":
    runtime = LocalWorkflowRunner(LocalFunctionWorkflowLoader(globals()))
    ctx = runtime.run("hello_world", "Joe")
    print(json.dumps(ctx, cls=WorkflowContextEncoder))
