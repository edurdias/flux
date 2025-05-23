from __future__ import annotations

from examples.multiple_pause_points import multi_pause_workflow
from flux.domain.events import ExecutionEventType


def test_first_pause():
    """Test that the workflow pauses at the first pause point."""
    ctx = multi_pause_workflow.run()

    # Workflow should be paused, not finished
    assert ctx.is_paused, "The workflow should be paused at the first checkpoint."
    assert not ctx.has_finished, "The workflow should not be finished while paused."

    # Check for the pause event
    pause_events = [e for e in ctx.events if e.type == ExecutionEventType.WORKFLOW_PAUSED]
    assert len(pause_events) == 1
    assert pause_events[0].value == "verify_setup"

    return ctx


def test_second_pause():
    """Test that the workflow pauses at the second pause point after resuming."""
    ctx = test_first_pause()
    ctx = multi_pause_workflow.run(execution_id=ctx.execution_id)

    # Should still be paused (at second point)
    assert ctx.is_paused, "The workflow should be paused at the second checkpoint."
    assert not ctx.has_finished, "The workflow should not be finished while paused."

    # Check for the pause events
    pause_events = [e for e in ctx.events if e.type == ExecutionEventType.WORKFLOW_PAUSED]
    # Filter out duplicate events by value (keeping only unique pause points)
    unique_pause_points = {e.value for e in pause_events}
    assert len(unique_pause_points) == 2
    assert "validate_data" in unique_pause_points

    return ctx


def test_third_pause():
    """Test that the workflow pauses at the third pause point (first chunk monitoring)."""
    ctx = test_second_pause()
    ctx = multi_pause_workflow.run(execution_id=ctx.execution_id)

    # Should still be paused (at third point)
    assert ctx.is_paused, "The workflow should be paused at the third checkpoint."
    assert not ctx.has_finished, "The workflow should not be finished while paused."

    # Check for the pause events
    pause_events = [e for e in ctx.events if e.type == ExecutionEventType.WORKFLOW_PAUSED]
    # Filter out duplicate events by value (keeping only unique pause points)
    unique_pause_points = {e.value for e in pause_events}
    assert len(unique_pause_points) == 3
    assert "monitor_progress_1" in unique_pause_points

    return ctx


def test_fourth_pause():
    """Test that the workflow pauses at the fourth pause point (second chunk monitoring)."""
    ctx = test_third_pause()
    ctx = multi_pause_workflow.run(execution_id=ctx.execution_id)

    # Should still be paused (at fourth point)
    assert ctx.is_paused, "The workflow should be paused at the fourth checkpoint."
    assert not ctx.has_finished, "The workflow should not be finished while paused."

    # Check for the pause events
    pause_events = [e for e in ctx.events if e.type == ExecutionEventType.WORKFLOW_PAUSED]
    # Filter out duplicate events by value (keeping only unique pause points)
    unique_pause_points = {e.value for e in pause_events}
    assert len(unique_pause_points) == 4
    assert "monitor_progress_2" in unique_pause_points

    return ctx


def test_fifth_pause():
    """Test that the workflow pauses at the fifth pause point (final approval)."""
    ctx = test_fourth_pause()
    ctx = multi_pause_workflow.run(execution_id=ctx.execution_id)

    # Should still be paused (at final approval)
    assert ctx.is_paused, "The workflow should be paused at the final approval point."
    assert not ctx.has_finished, "The workflow should not be finished while paused."

    # Check for the pause events
    pause_events = [e for e in ctx.events if e.type == ExecutionEventType.WORKFLOW_PAUSED]
    # Filter out duplicate events by value (keeping only unique pause points)
    unique_pause_points = {e.value for e in pause_events}
    assert len(unique_pause_points) == 5
    assert "final_approval" in unique_pause_points

    return ctx


def test_complete_workflow():
    """Test that the workflow completes after all pause points are resumed."""
    ctx = test_fifth_pause()
    ctx = multi_pause_workflow.run(execution_id=ctx.execution_id)

    # Should be finished and succeeded
    assert not ctx.is_paused, "The workflow should not be paused anymore."
    assert ctx.has_finished, "The workflow should be finished."
    assert ctx.has_succeeded, "The workflow should have succeeded."

    # Check the final output
    assert ctx.output["stage"] == "complete"
    assert ctx.output["chunks_processed"] == 3
    assert ctx.output["message"] == "Workflow completed successfully"

    return ctx


def test_full_resume_sequence():
    """Test the full sequence of pausing and resuming the workflow."""
    # First run
    ctx = multi_pause_workflow.run()
    assert ctx.is_paused and not ctx.has_finished

    # Resume multiple times, checking state at each point
    pause_points = [
        "verify_setup",
        "validate_data",
        "monitor_progress_1",
        "monitor_progress_2",
        "final_approval",
    ]

    for i, point in enumerate(pause_points):
        # Check current pause point
        pause_events = [e for e in ctx.events if e.type == ExecutionEventType.WORKFLOW_PAUSED]
        # Filter out duplicate events by value (keeping only unique pause points)
        unique_pause_points = [e.value for e in pause_events]
        assert point in unique_pause_points
        # Make sure we have at least i+1 unique pause points
        assert len(set(unique_pause_points)) >= i + 1

        # Resume to next point
        ctx = multi_pause_workflow.run(execution_id=ctx.execution_id)

    # Final state should be complete
    assert ctx.has_finished and ctx.has_succeeded
    assert ctx.output["stage"] == "complete"
    assert ctx.output["chunks_processed"] == 3
