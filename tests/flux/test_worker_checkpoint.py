"""Tests for the worker checkpoint outbox: retry, coalescing, terminal delivery, reauth."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from flux.worker import Worker


def make_worker() -> Worker:
    worker = Worker.__new__(Worker)
    worker.name = "test-worker"
    worker.base_url = "http://localhost:19000/workers"
    worker.session_token = "tok"
    worker.client = AsyncMock()
    worker._running_workflows = {}
    worker._checkpoint_outboxes = {}
    worker._registered = True
    worker._reauth_lock = asyncio.Lock()
    worker._checkpoint_retry_max_delay = 0.01
    worker._terminal_checkpoint_deadline = 5
    worker._progress_queues = {}
    worker._progress_flushers = {}
    worker._module_cache = {}
    worker._module_cache_ttl = 0
    return worker


def make_ctx(execution_id: str = "exec-1", finished: bool = False) -> MagicMock:
    ctx = MagicMock()
    ctx.execution_id = execution_id
    ctx.workflow_name = "test_wf"
    ctx.workflow_namespace = "default"
    ctx.has_finished = finished
    ctx.state.value = "COMPLETED" if finished else "RUNNING"
    ctx.events = []
    ctx.to_dict.return_value = {"execution_id": execution_id, "finished": finished}
    return ctx


def ok_response() -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status = MagicMock()
    response.json.return_value = {"status": "ok"}
    return response


@pytest.mark.asyncio
async def test_intermediate_checkpoint_retries_until_delivered():
    """A failed intermediate checkpoint is retried, not dropped."""
    worker = make_worker()
    attempts = 0

    async def flaky_post(url, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionError("server unreachable")
        return ok_response()

    worker.client.post = flaky_post

    ctx = make_ctx()
    await worker._checkpoint(ctx)

    box = worker._checkpoint_outboxes[ctx.execution_id]
    await asyncio.wait_for(_wait_for_ack(box), timeout=5)
    assert attempts == 3


@pytest.mark.asyncio
async def test_terminal_checkpoint_blocks_until_delivered():
    """A terminal checkpoint blocks the caller until the server acknowledges it."""
    worker = make_worker()
    attempts = 0

    async def flaky_post(url, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("server unreachable")
        return ok_response()

    worker.client.post = flaky_post

    ctx = make_ctx(finished=True)
    await worker._checkpoint(ctx)

    assert attempts == 2
    assert ctx.execution_id not in worker._checkpoint_outboxes


@pytest.mark.asyncio
async def test_terminal_checkpoint_gives_up_at_deadline_without_raising():
    """Persistent failure past the deadline logs and returns; it must not raise."""
    worker = make_worker()
    worker._terminal_checkpoint_deadline = 0.1
    worker.client.post = AsyncMock(side_effect=ConnectionError("server unreachable"))

    ctx = make_ctx(finished=True)
    await worker._checkpoint(ctx)

    assert ctx.execution_id not in worker._checkpoint_outboxes


@pytest.mark.asyncio
async def test_rapid_checkpoints_never_cancel_inflight_sends():
    """Rapid successive checkpoints coalesce to the newest snapshot; in-flight
    sends complete instead of being cancelled, and the terminal snapshot wins."""
    worker = make_worker()
    started = 0
    completed = 0
    sent_payloads = []

    async def slow_post(url, **kwargs):
        nonlocal started, completed
        started += 1
        await asyncio.sleep(0.02)
        completed += 1
        sent_payloads.append(kwargs["json"])
        return ok_response()

    worker.client.post = slow_post

    for _ in range(3):
        await worker._checkpoint(make_ctx())
    await worker._checkpoint(make_ctx(finished=True))

    assert started == completed  # nothing cancelled mid-flight
    assert sent_payloads[-1]["finished"] is True
    assert "exec-1" not in worker._checkpoint_outboxes


@pytest.mark.asyncio
async def test_close_outbox_drains_pending_non_terminal_snapshot():
    """Closing the outbox (pause / handler end) still delivers pending data."""
    worker = make_worker()
    gate = asyncio.Event()
    sent = []

    async def gated_post(url, **kwargs):
        await gate.wait()
        sent.append(kwargs["json"])
        return ok_response()

    worker.client.post = gated_post

    ctx = make_ctx()
    await worker._checkpoint(ctx)
    worker._close_checkpoint_outbox(ctx.execution_id)
    gate.set()

    box_sender = worker._checkpoint_outboxes.get(ctx.execution_id)
    await asyncio.sleep(0.05)
    assert sent, "pending snapshot was dropped on close"
    assert ctx.execution_id not in worker._checkpoint_outboxes
    assert box_sender is not None


@pytest.mark.asyncio
async def test_authorized_post_reregisters_on_401_and_retries():
    """A 401 mid-operation triggers one re-registration and a retry with the new token."""
    worker = make_worker()
    tokens_seen = []

    rejected = MagicMock()
    rejected.status_code = 401

    async def post(url, headers=None, **kwargs):
        tokens_seen.append(headers["Authorization"])
        if headers["Authorization"] == "Bearer tok":
            return rejected
        return ok_response()

    worker.client.post = post

    async def fake_register():
        worker.session_token = "new-tok"
        worker._registered = True

    worker._register = fake_register

    response = await worker._authorized_post("http://localhost:19000/workers/x/pong")

    assert response.status_code == 200
    assert tokens_seen == ["Bearer tok", "Bearer new-tok"]


@pytest.mark.asyncio
async def test_authorized_post_returns_non_auth_errors_unchanged():
    """Non-auth failures (e.g. 409) pass through without re-registration."""
    worker = make_worker()
    conflict = MagicMock()
    conflict.status_code = 409
    worker.client.post = AsyncMock(return_value=conflict)
    worker._register = AsyncMock()

    response = await worker._authorized_post("http://localhost:19000/workers/x/claim/e1")

    assert response.status_code == 409
    worker._register.assert_not_called()


async def _wait_for_ack(box):
    while box.acked < box.generation:
        await asyncio.sleep(0.005)
