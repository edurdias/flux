from __future__ import annotations

from flux import WorkflowExecutionContext
from flux.decorators import task
from flux.decorators import workflow


@task
def say_hello(name: str):
    return f"Hello, {name}"


@workflow
async def hello_world(ctx: WorkflowExecutionContext[str]):
    if not ctx.input:
        raise TypeError("Input not provided")
    return await say_hello(ctx.input)


if __name__ == "__main__":  # pragma: no cover
    ctx = hello_world.run("Joe")
    print(ctx.to_json())
