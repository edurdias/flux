from flux import activity, workflow
from flux.loaders import LocalFunctionWorkflowLoader
from flux.context import WorkflowExecutionContext
from flux.runners import LocalWorkflowRunner


@activity
def say_hello(name: str):
    return f"Hello, {name}"


@workflow
def hello_world(ctx: WorkflowExecutionContext[str]):
    return (yield say_hello(ctx.input))


if __name__ == "__main__":
    runner = LocalWorkflowRunner(LocalFunctionWorkflowLoader(globals()))
    ctx = runner.run_workflow("hello_world", "Joe")
    print(ctx.output)
