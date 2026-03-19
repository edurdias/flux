"""Tests for the ExecutionContext module."""

from __future__ import annotations

import asyncio
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


def test_emit_progress_default_callback_is_noop():
    ctx = ExecutionContext(workflow_id="wf1", workflow_name="test")
    asyncio.run(ctx.emit_progress("task_1", "my_task", {"step": 1}))


def test_emit_progress_calls_callback():
    captured = []

    def on_progress(execution_id, task_id, task_name, value):
        captured.append((execution_id, task_id, task_name, value))

    ctx = ExecutionContext(workflow_id="wf1", workflow_name="test", execution_id="exec_1")
    ctx.set_progress_callback(on_progress)
    asyncio.run(ctx.emit_progress("task_1", "my_task", {"step": 1}))

    assert len(captured) == 1
    assert captured[0] == ("exec_1", "task_1", "my_task", {"step": 1})


def test_emit_progress_does_not_add_to_events():
    ctx = ExecutionContext(workflow_id="wf1", workflow_name="test")
    ctx.set_progress_callback(lambda *_: None)
    asyncio.run(
        ctx.emit_progress("task_1", "my_task", {"data": "x"}),
    )
    assert len(ctx.events) == 0


def test_emit_progress_supports_async_callback():
    captured = []

    async def on_progress(execution_id, task_id, task_name, value):
        captured.append(value)

    ctx = ExecutionContext(workflow_id="wf1", workflow_name="test")
    ctx.set_progress_callback(on_progress)
    asyncio.run(ctx.emit_progress("task_1", "my_task", "hello"))
    assert captured == ["hello"]
