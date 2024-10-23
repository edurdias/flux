from __future__ import annotations

from flux import task
from flux import workflow
from flux import WorkflowExecutionContext
from flux.tasks import parallel


@task
def say_hi(name: str):
    return f"Hi, {name}"


@task
def say_hello(name: str):
    return f"Hello, {name}"


@task
def diga_ola(name: str):
    return f"Ola, {name}"


@task
def saluda(name: str):
    return f"Hola, {name}"


@workflow
def hello_world(ctx: WorkflowExecutionContext[str]):
    results = yield parallel(
        lambda: say_hi(ctx.input),
        lambda: say_hello(ctx.input),
        lambda: diga_ola(ctx.input),
        lambda: saluda(ctx.input),
    )
    return results


if __name__ == "__main__":  # pragma: no cover
    ctx = hello_world.run("Joe")
    print(ctx.to_json())
