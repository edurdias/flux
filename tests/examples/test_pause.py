from __future__ import annotations

from examples.pause import pause_workflow


def test_should_pause():
    """Test that the workflow pauses correctly when the pause task is reached."""
    # Run the workflow for the first time
    ctx = pause_workflow.run()

    # Workflow should be paused, not finished
    assert ctx.paused, "The workflow should be paused."
    assert not ctx.finished, "The workflow should not be finished while paused."
    assert not ctx.succeeded, "The workflow should not be marked as succeeded while paused."
    assert not ctx.failed, "The workflow should not be marked as failed while paused."

    # When paused, the output should be None since there's no completed event yet
    assert ctx.output is None

    return ctx


def test_should_resume_and_complete():
    """Test that the workflow resumes correctly after a pause."""
    # First run the workflow to get it into a paused state
    paused_ctx = test_should_pause()

    # Now resume the workflow by running it again with the same execution_id
    resumed_ctx = pause_workflow.run(execution_id=paused_ctx.execution_id)

    # Workflow should now be finished and succeeded
    assert resumed_ctx.finished, "The workflow should have finished after resuming."
    assert resumed_ctx.succeeded, "The workflow should have succeeded after resuming."
    assert resumed_ctx.output == "Data processed and approved"

    return resumed_ctx


def test_should_skip_if_finished():
    """Test that the workflow is skipped if already finished."""
    # First run the workflow and resume it to completion
    finished_ctx = test_should_resume_and_complete()

    # Run it again with the same execution_id
    repeat_ctx = pause_workflow.run(execution_id=finished_ctx.execution_id)

    # Should have same execution_id and output
    assert finished_ctx.execution_id == repeat_ctx.execution_id
    assert finished_ctx.output == repeat_ctx.output
    assert repeat_ctx.output == "Data processed and approved"
