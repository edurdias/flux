from examples.pipeline import pipeline_workflow
from flux.events import ExecutionEventType


def test_should_succeed():
    ctx = pipeline_workflow.run(5)
    assert (
        ctx.finished and ctx.succeeded
    ), "The workflow should have be completed and succeed."
    assert ctx.output == 169


def test_should_fail():
    ctx = pipeline_workflow.run()
    last_event = ctx.events[-1]
    assert last_event.type == ExecutionEventType.WORKFLOW_FAILED
    assert isinstance(last_event.value, TypeError)
