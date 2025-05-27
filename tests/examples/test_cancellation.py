"""Tests for the cancellation example."""

from __future__ import annotations

import asyncio
import pytest

from examples.cancellation import cancellable_workflow
from flux import ExecutionContext
from flux.domain.events import ExecutionState


class TestCancellationExample:
    """Tests for the cancellation example."""

    @pytest.mark.asyncio
    async def test_cancellable_workflow_completes(self):
        """Test that the workflow completes normally if not cancelled."""
        # Create the execution context with a small number of iterations
        ctx = ExecutionContext(
            workflow_id="cancellable_workflow",
            workflow_name="cancellable_workflow",
            input={"iterations": 1},  # Use just 1 iteration for faster test
        )

        # Create a mock checkpoint function
        async def mock_checkpoint(context):
            return context

        ctx.set_checkpoint(mock_checkpoint)

        # Run the workflow with a timeout
        try:
            result_ctx = await asyncio.wait_for(cancellable_workflow(ctx), timeout=3)

            # Verify the workflow completed normally
            assert result_ctx.state == ExecutionState.COMPLETED
            assert result_ctx.has_finished
            assert result_ctx.has_succeeded
            assert not result_ctx.is_cancelled
            assert isinstance(result_ctx.output, list)
            assert len(result_ctx.output) == 1
        except asyncio.TimeoutError:
            pytest.fail("Test timed out")

    @pytest.mark.asyncio
    async def test_cancellable_workflow_gets_cancelled(self):
        """Test that the workflow can be cancelled."""
        # Create the execution context with a larger number of iterations
        ctx = ExecutionContext(
            workflow_id="cancellable_workflow",
            workflow_name="cancellable_workflow",
            input={"iterations": 10},  # Enough iterations to allow for cancellation
        )

        # Create a mock checkpoint function
        async def mock_checkpoint(context):
            return context

        ctx.set_checkpoint(mock_checkpoint)

        # Run the workflow in a task so we can cancel it
        task = asyncio.create_task(cancellable_workflow(ctx))

        # Give the workflow a chance to start
        await asyncio.sleep(0.5)

        # Cancel the task
        task.cancel()

        # Wait for the task to complete or raise an exception
        with pytest.raises(asyncio.CancelledError):
            await task

        # Verify the workflow was cancelled
        assert ctx.state == ExecutionState.CANCELLED
        assert ctx.is_cancelled

    @pytest.mark.asyncio
    async def test_long_running_task_cancellation(self):
        """Test that the long_running_task can be cancelled."""
        from examples.cancellation import long_running_task
        from flux.domain.execution_context import CURRENT_CONTEXT

        # Create a context for the task
        ctx = ExecutionContext(
            workflow_id="test_long_running_task",
            workflow_name="test_long_running_task",
        )

        # Set the context in the task context variable
        CURRENT_CONTEXT.set(ctx)

        try:
            # Create a task that we'll cancel
            task = asyncio.create_task(long_running_task(20))

            # Give it a chance to start
            await asyncio.sleep(0.5)

            # Cancel the task
            task.cancel()

            # Verify the task was cancelled
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            # Clean up the context
            CURRENT_CONTEXT.set(None)
