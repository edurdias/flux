import json
from pprint import pprint

from flux.activity import activity
from flux.loaders import LocalFunctionWorkflowLoader
from flux.workflow import workflow
from flux.context import WorkflowExecutionContext
from flux.encoders import WorkflowContextEncoder
from flux.runner import WorkflowRunner


@activity
def say_hello(name: str):
    return f"Hello, {name}"


@workflow
def hello_world(ctx: WorkflowExecutionContext[str]):
    hello_you = yield from say_hello(ctx.input)
    hello_world = yield from say_hello("World")
    return [hello_you, hello_world]


runtime = WorkflowRunner(LocalFunctionWorkflowLoader(globals()))
ctx = runtime.run("hello_world", "Joe")

print(ctx.output)
