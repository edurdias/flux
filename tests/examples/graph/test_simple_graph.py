from examples.graph.simple_graph import simple_graph
from flux.events import ExecutionEventType


def test_should_succeed():
    ctx = simple_graph.run("Joe")
    assert (
        ctx.finished and ctx.succeeded
    ), "The workflow should have been completed successfully."
    assert ctx.output == "Hello, Joe"


def test_should_fail():
    ctx = simple_graph.run()
    last_event = ctx.events[-1]
    assert last_event.type == ExecutionEventType.WORKFLOW_FAILED
    assert isinstance(last_event.value, TypeError)
