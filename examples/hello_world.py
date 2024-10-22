from __future__ import annotations

from flux import task
from flux import workflow
from flux import WorkflowExecutionContext


@task
def say_hello(name: str):
    return f"Hello, {name}"


@workflow
def hello_world(ctx: WorkflowExecutionContext[str]):
    if not ctx.input:
        raise TypeError('Input not provided')
    return (yield say_hello(ctx.input))


if __name__ == '__main__':  # pragma: no cover
    ctx = hello_world.run('Joe')
    print(ctx.to_json())
