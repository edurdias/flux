from examples.hello_world import hello_world
from flux.events import ExecutionEventType


def test_should_succeed():
    ctx = hello_world.run("Joe")
    assert (
        ctx.finished and ctx.succeeded
    ), "The workflow should have be completed and succeed."
    assert ctx.output == "Hello, Joe"


def test_should_replay():
    first_ctx = hello_world.run("Joe")
    assert first_ctx.output == "Hello, Joe"

    second_ctx = hello_world.run("Joe", execution_id=first_ctx.execution_id)
    assert first_ctx.output == second_ctx.output
    assert first_ctx.events[-1] == second_ctx.events[-1]


def test_should_fail():
    ctx = hello_world.run()
    last_event = ctx.events[-1]
    assert last_event.type == ExecutionEventType.WORKFLOW_FAILED
    assert isinstance(last_event.value, TypeError)
