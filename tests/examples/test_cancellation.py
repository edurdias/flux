from __future__ import annotations


from examples.cancellation import cancellable_workflow
from flux.domain.events import ExecutionEventType, ExecutionState
from flux.domain.execution_context import ExecutionContext
from flux.context_managers import ContextManager


def test_has_canceled_property():
    """Test the has_canceled property of ExecutionContext"""
    # Create a context and mark it as canceled
    ctx = ExecutionContext(
        workflow_id="test_workflow",
        workflow_name="test_workflow",
        input=None,
        execution_id="test-id",
    )

    # Initially should not be canceled
    assert not ctx.has_canceled

    # Cancel the context
    ctx.cancel("test", "Test cancellation")

    # Check that it's now marked as canceled
    assert ctx.has_canceled
    assert ctx.has_finished
    assert ctx.state == ExecutionState.CANCELED

    # Verify the event was added
    assert len(ctx.events) == 1
    assert ctx.events[0].type == ExecutionEventType.WORKFLOW_CANCELED
    assert ctx.events[0].value == "Test cancellation"


def test_pre_execution_cancellation():
    """Test cancellation of a workflow before it starts executing tasks"""
    # First, start a workflow
    ctx = cancellable_workflow.run()

    # Get the execution context and cancel it
    manager = ContextManager.create()
    execution_ctx = manager.get(ctx.execution_id)
    execution_ctx.cancel("test", "Pre-execution cancellation")

    # Save it back
    manager.save(execution_ctx)

    # Now try to resume the workflow - should recognize that it's already finished
    resumed_ctx = cancellable_workflow.run(execution_id=ctx.execution_id)

    # Verify it's marked as canceled and didn't run
    assert resumed_ctx.has_canceled
    assert resumed_ctx.state == ExecutionState.CANCELED
    assert any(e.type == ExecutionEventType.WORKFLOW_CANCELED for e in resumed_ctx.events)


def test_in_flight_cancellation():
    """Test cancellation of a workflow while a task is executing"""
    # Create a simple test to verify that cancellation works
    # This is a simplified test that doesn't try to mock workflow execution

    # Create a context and check that cancellation event works
    ctx = ExecutionContext(
        workflow_id="test_workflow",
        workflow_name="test_workflow",
        input=None,
        execution_id="test-in-flight-id",
    )

    # Verify cancel_event is initially not set
    assert not ctx.cancel_event.is_set()

    # Set cancellation
    ctx.set_cancellation()

    # Verify cancel_event is now set
    assert ctx.cancel_event.is_set()

    # Cancel the context
    ctx.cancel("test", "In-flight cancellation test")

    # Verify state is CANCELED
    assert ctx.state == ExecutionState.CANCELED
    assert ctx.has_canceled


if __name__ == "__main__":
    test_has_canceled_property()
    test_pre_execution_cancellation()
    # asyncio.run(test_in_flight_cancellation())
