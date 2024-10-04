import json

from flux import task, workflow
from flux.encoders import WorkflowContextEncoder
from flux.catalogs import LocalWorkflowCatalog
from flux.context import WorkflowExecutionContext
from flux.runners import LocalWorkflowRunner


@task
def say_hello(name: str):
    return f"Hello, {name}"


@workflow
def hello_world(ctx: WorkflowExecutionContext[str]):
    return (yield say_hello(ctx.input))


if __name__ == "__main__":
    runner = LocalWorkflowRunner(LocalWorkflowCatalog(globals()))
    ctx = runner.run_workflow("hello_world", "Joe")
    print(ctx.output)
    print(json.dumps(ctx, indent=4, cls=WorkflowContextEncoder))
