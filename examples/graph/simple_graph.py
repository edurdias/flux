from __future__ import annotations

from flux.context import WorkflowExecutionContext
from flux.decorators import task
from flux.decorators import workflow
from flux.tasks import Graph


@task
def get_name(input: str) -> str:
    return input


@task
def say_hello(name: str) -> str:
    return f"Hello, {name}"


@workflow
def simple_graph(ctx: WorkflowExecutionContext[str]):
    if not ctx.input:
        raise TypeError("Input not provided")

    hello = (
        Graph("hello_world")
        .add_node("get_name", get_name)
        .add_node("say_hello", say_hello)
        .add_edge("get_name", "say_hello")
        .start_with("get_name")
        .end_with("say_hello")
    )
    return (yield hello(ctx.input))


if __name__ == "__main__":  # pragma: no cover
    ctx = simple_graph.run("Joe")
    print(ctx.to_json())