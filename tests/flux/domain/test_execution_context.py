"""Tests for the ExecutionContext module."""

from __future__ import annotations

from unittest.mock import MagicMock

from flux.domain.execution_context import ExecutionContext
from flux.domain.events import ExecutionEventType, ExecutionState
from flux.worker_registry import WorkerInfo


class TestExecutionContext:
    """Tests for the ExecutionContext class."""

    def test_creation(self):
        """Test creation of execution context."""
        ctx = ExecutionContext(workflow_id="test-workflow", workflow_name="test")
        assert ctx.workflow_id == "test-workflow"
        assert ctx.workflow_name == "test"
        assert ctx.state == ExecutionState.CREATED
        assert not ctx.has_finished
        assert not ctx.has_succeeded
        assert not ctx.has_failed
        assert not ctx.is_cancelled
        assert not ctx.is_cancelling

    def test_start_cancel(self):
        """Test starting cancellation process."""
        ctx = ExecutionContext(workflow_id="test-workflow", workflow_name="test")

        # Set a worker for the context
        worker = MagicMock(spec=WorkerInfo)
        worker.name = "test-worker"
        ctx.claim(worker)

        # Start cancellation
        ctx.start_cancel()

        # Verify state changes
        assert ctx.state == ExecutionState.CANCELLING
        assert ctx.is_cancelling
        assert not ctx.is_cancelled
        assert not ctx.has_finished

        # Verify event was created
        assert len(ctx.events) > 0
        cancel_events = [e for e in ctx.events if e.type == ExecutionEventType.WORKFLOW_CANCELLING]
        assert len(cancel_events) == 1
        assert cancel_events[0].source_id == "test-worker"
        assert cancel_events[0].name == "test"

    def test_cancel(self):
        """Test complete cancellation process."""
        ctx = ExecutionContext(workflow_id="test-workflow", workflow_name="test")

        # Set a worker for the context
        worker = MagicMock(spec=WorkerInfo)
        worker.name = "test-worker"
        ctx.claim(worker)

        # Cancel (this will automatically call start_cancel if needed)
        ctx.cancel()

        # Verify state changes
        assert ctx.state == ExecutionState.CANCELLED
        assert ctx.is_cancelled
        assert not ctx.is_cancelling
        assert ctx.has_finished

        # Verify events were created
        cancel_events = [e for e in ctx.events if e.type == ExecutionEventType.WORKFLOW_CANCELLED]
        assert len(cancel_events) == 1
        assert cancel_events[0].source_id == "test-worker"
        assert cancel_events[0].name == "test"

    def test_cancel_with_prior_start_cancel(self):
        """Test cancellation after start_cancel has been called."""
        ctx = ExecutionContext(workflow_id="test-workflow", workflow_name="test")

        # Set a worker for the context
        worker = MagicMock(spec=WorkerInfo)
        worker.name = "test-worker"
        ctx.claim(worker)

        # Start cancellation first
        ctx.start_cancel()

        # Then complete cancellation
        ctx.cancel()

        # Verify state changes
        assert ctx.state == ExecutionState.CANCELLED
        assert ctx.is_cancelled
        assert not ctx.is_cancelling
        assert ctx.has_finished

        # Verify both events were created
        cancel_events = [
            e
            for e in ctx.events
            if e.type
            in (ExecutionEventType.WORKFLOW_CANCELLING, ExecutionEventType.WORKFLOW_CANCELLED)
        ]
        assert len(cancel_events) == 2
