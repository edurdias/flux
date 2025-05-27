"""Tests for the ContextManager cancellation methods."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock
from sqlalchemy.sql import text

from flux import ExecutionContext
from flux.context_managers import ContextManager, SQLiteContextManager
from flux.domain.events import ExecutionState
from flux.worker_registry import WorkerInfo


@pytest.fixture
def clean_context_manager():
    """Fixture for creating a clean context manager with test isolation."""
    # Create the context manager
    manager = ContextManager.create()

    # Clean up any existing test contexts
    if isinstance(manager, SQLiteContextManager):
        with manager.session() as session:
            # Delete any test contexts from previous test runs
            session.execute(text("DELETE FROM executions WHERE workflow_name = 'test'"))
            session.commit()

    return manager


@pytest.fixture
def worker_info():
    """Fixture for creating a worker info."""
    worker = MagicMock(spec=WorkerInfo)
    worker.name = "test-worker"
    worker.session_token = "test-token"
    worker.resources = {
        "cpu_total": 4,
        "cpu_available": 2,
        "memory_total": 8000,
        "memory_available": 4000,
        "disk_total": 100000,
        "disk_free": 50000,
        "gpus": [],
    }
    worker.packages = [{"name": "pytest", "version": "1.0.0"}]
    return worker


class TestContextManagerCancellation:
    """Tests for ContextManager cancellation methods."""

    def test_next_cancellation_returns_none_when_no_cancellation(
        self,
        clean_context_manager,
        worker_info,
    ):
        """Test that next_cancellation returns None when no cancellation is pending."""
        # This test assumes there are no cancellations pending in the test database
        result = clean_context_manager.next_cancellation(worker_info)
        assert result is None

    def test_next_cancellation_returns_context_when_cancellation_pending(
        self,
        clean_context_manager,
        worker_info,
    ):
        """Test that next_cancellation returns the context when a cancellation is pending."""
        # Create a context that is in CANCELLING state
        ctx = ExecutionContext(
            workflow_id="test-workflow",
            workflow_name="test",
            current_worker=worker_info.name,
        )
        ctx.start_cancel()
        saved_ctx = clean_context_manager.save(ctx)

        # Get the next cancellation
        result = clean_context_manager.next_cancellation(worker_info)

        # Verify we got the context back
        assert result is not None
        assert result.execution_id == saved_ctx.execution_id
        assert result.state == ExecutionState.CANCELLING

    def test_save_preserves_cancellation_state(self, clean_context_manager, worker_info):
        """Test that saving a context preserves its cancellation state."""
        # Create a context that is in CANCELLING state
        ctx = ExecutionContext(
            workflow_id="test-workflow",
            workflow_name="test",
            current_worker=worker_info.name,
        )
        ctx.start_cancel()

        # Save it
        saved_ctx = clean_context_manager.save(ctx)

        # Verify the state was preserved
        assert saved_ctx.state == ExecutionState.CANCELLING
        assert saved_ctx.is_cancelling

        # Cancel it
        saved_ctx.cancel()
        final_ctx = clean_context_manager.save(saved_ctx)

        # Verify the state was updated
        assert final_ctx.state == ExecutionState.CANCELLED
        assert final_ctx.is_cancelled
