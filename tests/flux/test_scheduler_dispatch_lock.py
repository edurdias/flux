"""Tests for the scheduler dispatch singleton (cross-replica advisory lock).

The lock ensures that with multiple server replicas only one dispatches due
schedules per cycle, so the same schedule cannot fire on every replica.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from flux.config import Configuration
from flux.schedule_manager import create_schedule_manager

_DB_URL = os.environ.get("FLUX_DATABASE_URL", "")


@pytest.fixture
def manager(tmp_path):
    Configuration.get().override(database_url=f"sqlite:///{tmp_path / 'sched.db'}")
    from flux.models import DatabaseRepository

    DatabaseRepository._engines.clear()
    yield create_schedule_manager()
    DatabaseRepository._engines.clear()


def test_dispatch_lock_yields_true_on_sqlite(manager):
    # SQLite is single-node: the lone server is always the dispatcher.
    with manager.dispatch_lock() as is_dispatcher:
        assert is_dispatcher is True


def test_dispatch_lock_reentrant_on_sqlite(manager):
    # No real lock on SQLite, so nested acquisition is allowed (single node).
    with manager.dispatch_lock() as outer:
        assert outer is True
        with manager.dispatch_lock() as inner:
            assert inner is True


def _cm(value):
    @contextmanager
    def _factory():
        yield value

    return _factory


async def _run_one_scheduler_cycle(server, mock_manager):
    """Drive Server._scheduler_loop through exactly one iteration."""
    server.scheduler_running = True
    server.poll_interval = 0

    call_count = 0

    async def sleep_then_cancel(delay):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return
        raise asyncio.CancelledError()

    with (
        patch("flux.server.create_schedule_manager", return_value=mock_manager),
        patch("asyncio.sleep", side_effect=sleep_then_cancel),
    ):
        await server._scheduler_loop()


@pytest.mark.asyncio
async def test_scheduler_loop_skips_dispatch_when_not_lock_holder():
    from flux.server import Server

    server = Server("127.0.0.1", 0)
    mock_manager = MagicMock()
    mock_manager.dispatch_lock = _cm(False)

    await _run_one_scheduler_cycle(server, mock_manager)

    # Lock not held → this replica must not read or trigger schedules.
    mock_manager.get_due_schedules.assert_not_called()


@pytest.mark.asyncio
async def test_scheduler_loop_dispatches_when_lock_holder():
    from flux.server import Server

    server = Server("127.0.0.1", 0)
    mock_manager = MagicMock()
    mock_manager.dispatch_lock = _cm(True)
    mock_manager.get_due_schedules.return_value = []

    await _run_one_scheduler_cycle(server, mock_manager)

    # Lock held → this replica reads due schedules (none here, so no triggers).
    mock_manager.get_due_schedules.assert_called_once()


@pytest.mark.postgresql
@pytest.mark.skipif(
    not _DB_URL.startswith("postgresql://"),
    reason="requires a PostgreSQL FLUX_DATABASE_URL",
)
def test_dispatch_lock_is_exclusive_on_postgres():
    """A second concurrent acquirer is denied while the first holds the lock."""
    from flux.models import DatabaseRepository

    DatabaseRepository._engines.clear()
    try:
        manager = create_schedule_manager()
        with manager.dispatch_lock() as first:
            assert first is True
            # A distinct connection cannot take the session-scoped lock.
            with manager.dispatch_lock() as second:
                assert second is False
        # Once released, the lock is grantable again.
        with manager.dispatch_lock() as again:
            assert again is True
    finally:
        DatabaseRepository._engines.clear()
