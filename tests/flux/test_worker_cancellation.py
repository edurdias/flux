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
    async def test_handle_execution_cancelled_defers_while_claim_in_flight(
        self,
        worker,
        execution_context,
    ):
        """Between the claim POST and task registration the execution is owned
        here but absent from _running_workflows. Resolving it in that window
        would write CANCELLED, stop re-delivery, and let the body then run to
        completion with every checkpoint rejected — so the handler must defer
        to the next delivery instead.
        """
        worker._running_workflows = {}
        worker._claiming = {"test-execution-id"}
        execution_context.start_cancel()

        mock_event = MagicMock()
        mock_event.json.return_value = {"context": execution_context.to_dict()}

        await worker._handle_execution_cancelled(mock_event)

        assert worker._checkpoint.await_count == 0, (
            "an in-flight claim must not be resolved as CANCELLED"
        )

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

    @pytest.mark.asyncio
    async def test_a_checkpoint_that_outlives_the_wait_is_still_observed(self, caplog):
        """On timeout the shielded write keeps running with nobody awaiting it.
        An exception it raises then would otherwise surface only as asyncio's
        "Task exception was never retrieved" at GC time, detached from the
        execution it belongs to."""
        import logging

        from flux.workflow import _report_detached_checkpoint

        async def _boom():
            raise RuntimeError("checkpoint exploded")

        task = asyncio.ensure_future(_boom())
        await asyncio.sleep(0)

        with caplog.at_level(logging.ERROR):
            _report_detached_checkpoint(task, "exec-1")

        assert "checkpoint exploded" in caplog.text
        assert "exec-1" in caplog.text

    @pytest.mark.asyncio
    async def test_timeout_detaches_the_write_and_reports_its_landing(
        self,
        caplog,
        monkeypatch,
    ):
        """Drives _checkpoint_cancelled itself into its timeout, covering the
        callback-attach wiring the test above only exercises directly."""
        import logging
        import sys

        from flux.workflow import workflow

        # flux.workflow the *attribute* resolves to the class; the module
        # global being patched lives on the module object in sys.modules.
        monkeypatch.setattr(sys.modules["flux.workflow"], "CANCELLED_CHECKPOINT_TIMEOUT", 0.01)
        landed = asyncio.Event()

        async def _slow_checkpoint(ctx):
            await asyncio.sleep(0.1)
            landed.set()

        ctx = ExecutionContext(
            workflow_id="default/_slow",
            workflow_namespace="default",
            workflow_name="_slow",
            checkpoint=_slow_checkpoint,
        )

        with caplog.at_level(logging.INFO):
            await workflow._checkpoint_cancelled(ctx)
            assert "ended early" in caplog.text, "the bounded wait must give up at the timeout"
            assert not landed.is_set()

            await asyncio.wait_for(landed.wait(), timeout=5)
            await asyncio.sleep(0)  # let the done-callback run

        assert "landed late" in caplog.text, "the detached write's outcome must be reported"

    @pytest.mark.asyncio
    async def test_a_failing_checkpoint_does_not_mask_the_cancellation(self, caplog):
        """Inline mode saves straight to the database, so a save failure lands
        in _checkpoint_cancelled directly. It must be logged and swallowed —
        the original CancelledError keeps unwinding."""
        import logging

        from flux.workflow import workflow

        async def _failing_checkpoint(ctx):
            raise RuntimeError("db unavailable")

        ctx = ExecutionContext(
            workflow_id="default/_slow",
            workflow_namespace="default",
            workflow_name="_slow",
            checkpoint=_failing_checkpoint,
        )

        with caplog.at_level(logging.ERROR):
            await workflow._checkpoint_cancelled(ctx)

        assert "cancelled checkpoint failed" in caplog.text
        assert "db unavailable" in caplog.text
