from examples.graph.simple_graph import simple_graph
from flux.events import ExecutionEventType


def test_should_succeed():
    ctx = simple_graph.run("Joe")
    assert (
        ctx.finished and ctx.succeeded
    ), "The workflow should have been completed successfully."
    assert ctx.output == "Hello, Joe"
    
def test_should_replay():
    first_ctx = simple_graph.run("Joe")
    assert (
        first_ctx.finished and first_ctx.succeeded
    ), "The workflow should have been completed successfully."
    assert first_ctx.output == "Hello, Joe"

    second_ctx = simple_graph.run("Joe", execution_id=first_ctx.execution_id)
    assert first_ctx.execution_id == second_ctx.execution_id
    assert first_ctx.output == second_ctx.output


def test_should_fail():
    ctx = simple_graph.run()
    last_event = ctx.events[-1]
    assert last_event.type == ExecutionEventType.WORKFLOW_FAILED
    assert isinstance(last_event.value, TypeError)
