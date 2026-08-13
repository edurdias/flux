"""Tests for worker cancellation handling."""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from flux import ExecutionContext
from flux.domain import ExecutionState
from flux.worker import Worker


@pytest.fixture
def mock_client():
    """Mock HTTP client."""
    mock = AsyncMock()
    mock.post = AsyncMock()
    mock.post.return_value.json.return_value = {"session_token": "test-token"}
    mock.post.return_value.raise_for_status = AsyncMock()
    return mock


@pytest.fixture
def worker(mock_client):
    """Create a worker with mocked HTTP client."""
    with patch("flux.worker.httpx.AsyncClient", return_value=mock_client):
        worker = Worker(name="test-worker", server_url="http://localhost:8000")
        worker.session_token = "test-token"
        worker._checkpoint = AsyncMock()
        return worker


@pytest.fixture
def execution_context():
    """Create an execution context for testing."""
    return ExecutionContext(
        workflow_id="test-workflow",
        workflow_namespace="default",
        workflow_name="test",
        execution_id="test-execution-id",
        input="test-input",
    )


class TestWorkerCancellation:
    """Tests for worker cancellation handling."""

    @pytest.mark.asyncio
    async def test_handle_execution_cancelled(self, worker, execution_context):
        """Test that worker handles execution cancelled events correctly."""
        # Create a mock task to be cancelled
        mock_task = MagicMock()
        mock_task.cancel = MagicMock()

        async def mock_await():
            return None

        mock_task.__await__ = mock_await().__await__

        # Add the task to running workflows
        worker._running_workflows = {"test-execution-id": mock_task}

        # Set up the execution context to be cancelled
        execution_context.start_cancel()

        # Create a mock event
        mock_event = MagicMock()
        mock_event.json.return_value = {"context": execution_context.to_dict()}

        # Call the handler
        await worker._handle_execution_cancelled(mock_event)

        # Verify the task was cancelled
        mock_task.cancel.assert_called_once()

        # Verify the task was removed from running workflows
        assert "test-execution-id" not in worker._running_workflows

    @pytest.mark.asyncio
    async def test_handle_execution_cancelled_no_running_task(self, worker, execution_context):
        """A cancellation for an execution this worker is not running must
        still resolve the row.

        This asserted only "does not raise" before, which is precisely the
        behaviour that made issue #189 survive: the handler returned without
        writing anything, the row stayed CANCELLING, and the dispatcher re-sent
        the cancellation every cycle — 57,905 times in the run that finally
        captured logs.
        """
        worker._running_workflows = {}
        execution_context.start_cancel()

        mock_event = MagicMock()
        mock_event.json.return_value = {"context": execution_context.to_dict()}

        await worker._handle_execution_cancelled(mock_event)

        assert worker._checkpoint.await_count == 1, (
            "the row must be resolved, not silently left CANCELLING"
        )
        checkpointed = worker._checkpoint.await_args.args[0]
        assert checkpointed.state == ExecutionState.CANCELLED

    @pytest.mark.asyncio
    async def test_handle_execution_cancelled_task_already_done(self, worker, execution_context):
        """Test handling execution cancelled when task is already done."""
        # Create a mock task that raises CancelledError when awaited
        mock_task = MagicMock()
        mock_task.cancel = MagicMock()

        async def raise_cancelled_error():
            raise asyncio.CancelledError()

        mock_task.__await__ = raise_cancelled_error().__await__

        # Add the task to running workflows
        worker._running_workflows = {"test-execution-id": mock_task}

        # Set up the execution context to be cancelled
        execution_context.start_cancel()

        # Create a mock event
        mock_event = MagicMock()
        mock_event.json.return_value = {"context": execution_context.to_dict()}

        # Call the handler
        await worker._handle_execution_cancelled(mock_event)

        # Verify the task was cancelled
        mock_task.cancel.assert_called_once()

        # Verify the task was removed from running workflows
        assert "test-execution-id" not in worker._running_workflows


class TestCancelledCheckpointSurvivesASecondCancel:
    """The other half of issue #189, and the deeper one.

    `workflow.run` writes CANCELLED in a `finally` that awaits — while already
    unwinding from a delivered CancelledError. A second cancel interrupts that
    await and the terminal state is never written, so the row stays CANCELLING
    and gets re-dispatched, which cancels again. The dispatcher re-sends a
    cancellation every cycle for as long as the row is CANCELLING, so the
    second cancel is imminent by construction rather than a rare race.
    """

    @pytest.mark.asyncio
    async def test_terminal_state_persists_under_repeated_cancellation(self):
        from flux import task, workflow

        checkpoints: list[str] = []

        async def _checkpoint(ctx):
            # Yield, so a cancellation delivered now lands mid-write — which is
            # exactly what happened in CI.
            await asyncio.sleep(0)
            checkpoints.append(ctx.state.value)

        @task
        async def _forever():
            await asyncio.sleep(30)

        @workflow
        async def _slow(ctx: ExecutionContext):
            await _forever()
            return "done"

        ctx = ExecutionContext(
            workflow_id="default/_slow",
            workflow_namespace="default",
            workflow_name="_slow",
            checkpoint=_checkpoint,
        )

        running = asyncio.ensure_future(_slow(ctx))
        await asyncio.sleep(0.05)

        running.cancel()
        await asyncio.sleep(0)
        running.cancel()  # the second delivery, mid-checkpoint
        with pytest.raises(asyncio.CancelledError):
            await running

        # Give the shielded write its moment to land.
        await asyncio.sleep(0.05)

        assert "CANCELLED" in checkpoints, (
            "the terminal state was lost to the second cancel, leaving the row CANCELLING forever"
        )
