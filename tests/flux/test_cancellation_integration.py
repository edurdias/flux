"""Integration tests for cancellation feature."""

from __future__ import annotations

import asyncio
import pytest

from flux import ExecutionContext
from flux.domain.events import ExecutionState
from flux.workflow import workflow


# Define a workflow that can be cancelled
@workflow
async def cancellable_workflow(ctx: ExecutionContext):
    """A workflow that sleeps and can be cancelled."""
    # Sleep for a long time to allow cancellation
    try:
        await asyncio.sleep(10)
        return "Completed"
    except asyncio.CancelledError:
        # Let the workflow decorator handle the cancellation
        # by re-raising the exception (matching framework behavior)
        raise


class TestCancellationIntegration:
    """Integration tests for cancellation feature."""

    @pytest.mark.asyncio
    async def test_workflow_cancellation_end_to_end(self):
        """Test end-to-end workflow cancellation."""
        # Create the execution context
        ctx = ExecutionContext(
            workflow_id="cancellable_workflow",
            workflow_name="cancellable_workflow",
            input=None,
        )

        # Create a mock checkpoint function
        async def mock_checkpoint(context):
            return context

        ctx.set_checkpoint(mock_checkpoint)

        # Run the workflow in a task so we can cancel it
        task = asyncio.create_task(cancellable_workflow(ctx))

        # Give the workflow a chance to start
        await asyncio.sleep(0.5)

        # Request cancellation
        ctx.start_cancel()

        # Actually cancel the task (this is needed to propagate the cancellation)
        task.cancel()

        # Wait for the task to be cancelled and complete
        try:
            result_ctx = await asyncio.wait_for(task, timeout=2)

            # Verify the workflow was cancelled
            assert result_ctx.state == ExecutionState.CANCELLED
            assert result_ctx.is_cancelled
            assert result_ctx.has_finished

            # Check for the cancellation events
            cancel_events = [
                e for e in result_ctx.events if e.type.name.startswith("WORKFLOW_CANCEL")
            ]
            assert len(cancel_events) >= 1

        except asyncio.CancelledError:
            # Task was cancelled as expected
            # The context should already be in CANCELLED state due to the workflow's cancel() call
            # happening inside the workflow decorator when it catches CancelledError
            assert ctx.state == ExecutionState.CANCELLED
            assert ctx.is_cancelled
            assert ctx.has_finished

            # Check for the cancellation events
            cancel_events = [e for e in ctx.events if e.type.name.startswith("WORKFLOW_CANCEL")]
            assert len(cancel_events) >= 1

        except asyncio.TimeoutError:
            pytest.fail("Workflow did not cancel within timeout")

    @pytest.mark.asyncio
    async def test_workflow_completes_without_cancellation(self):
        """Test that workflow completes normally without cancellation."""

        # Create a simpler workflow that completes quickly
        @workflow
        async def quick_workflow(ctx: ExecutionContext):
            return "Quick result"

        # Create the execution context
        ctx = ExecutionContext(
            workflow_id="quick_workflow",
            workflow_name="quick_workflow",
            input=None,
        )

        # Create a mock checkpoint function
        async def mock_checkpoint(context):
            return context

        ctx.set_checkpoint(mock_checkpoint)

        # Run the workflow
        result_ctx = await quick_workflow(ctx)

        # Verify the workflow completed normally
        assert result_ctx.state == ExecutionState.COMPLETED
        assert not result_ctx.is_cancelled
        assert not result_ctx.is_cancelling
        assert result_ctx.has_finished
        assert result_ctx.has_succeeded
        assert result_ctx.output == "Quick result"
