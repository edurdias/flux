from flux.decorators import task, workflow
from flux.tasks import graph


@task
def get_name() -> str:
    return "Joe"


@task
def say_hello(name: str) -> str:
    return f"Hello, {name}"


@workflow
def graph_workflow():
    hello_graph = (
        graph("hello_world")
        .add_node("get_name", get_name)
        .add_node("say_hello", say_hello)
        .add_edge("get_name", "say_hello")
        .set_entry_point("get_name")
        .set_finish_point("say_hello")
    )
    return (yield hello_graph())


if __name__ == "__main__":

    ctx = graph_workflow.run()
    print(ctx.to_json())
