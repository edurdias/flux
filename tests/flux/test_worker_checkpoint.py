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
    worker._claim_generations = {}
    worker._transient_started = set()
    worker._registered = True
    worker._reauth_lock = asyncio.Lock()
    worker._checkpoint_retry_max_delay = 0.01
    worker._terminal_checkpoint_deadline = 5
    worker._progress_queues = {}
    worker._progress_flushers = {}
    worker._progress_channels = {}
    worker._runners = {}
    worker._default_runner = "subprocess"
    worker._draining = False
    worker._drain_timeout = 5
    worker._healthy = True
    worker._metrics_provider = None
    worker._metrics_interval = 10.0
    worker._metrics_snapshot = None
    worker._metrics_collected_at = None
    worker._metrics_collector = None
    worker._user_metrics = None
    worker._execution_started = {}
    worker._paused = False
    worker._control_server = None
    worker._control_socket_path = None
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


@pytest.mark.asyncio
async def test_drain_waits_for_running_executions():
    """Drain lets in-flight executions finish instead of cancelling them."""
    worker = make_worker()
    finished = []

    async def wf():
        await asyncio.sleep(0.05)
        finished.append(True)

    worker._running_workflows["e1"] = asyncio.create_task(wf())
    await worker._drain()
    assert finished == [True]


@pytest.mark.asyncio
async def test_drain_cancels_past_deadline():
    """Executions still running at the drain deadline are cancelled."""
    worker = make_worker()
    worker._drain_timeout = 0.05
    cancelled = []

    async def stuck():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    worker._running_workflows["e1"] = asyncio.create_task(stuck())
    await worker._drain()
    assert cancelled == [True]


@pytest.mark.asyncio
async def test_drain_flushes_outstanding_checkpoint_senders():
    """Terminal checkpoints still in flight get a final delivery window."""
    from flux.worker import _CheckpointOutbox

    worker = make_worker()
    delivered = []

    async def sender():
        await asyncio.sleep(0.05)
        delivered.append(True)

    box = _CheckpointOutbox()
    box.sender = asyncio.create_task(sender())
    worker._checkpoint_outboxes["e1"] = box
    await worker._drain()
    assert delivered == [True]


@pytest.mark.asyncio
async def test_scheduled_handler_declines_work_while_draining():
    """A dispatch that races the drain window is not claimed."""
    worker = make_worker()
    worker._draining = True
    worker._authorized_post = AsyncMock()

    evt = MagicMock()
    evt.json.return_value = {
        "workflow": {
            "id": "wf-1",
            "namespace": "default",
            "name": "wf",
            "version": 1,
            "source": "",
        },
        "context": {
            "workflow_id": "wf-1",
            "workflow_namespace": "default",
            "workflow_name": "wf",
            "execution_id": "exec-drain",
            "input": None,
            "state": "SCHEDULED",
            "events": [],
        },
    }
    await worker._handle_execution_scheduled("http://localhost:19000/workers/x", evt)
    worker._authorized_post.assert_not_called()


@pytest.mark.asyncio
async def test_fenced_checkpoint_aborts_local_execution():
    """A stale-claim 409 cancels the local run and unblocks terminal waiters."""
    worker = make_worker()
    worker._claim_generations["exec-1"] = "1"

    fenced = MagicMock()
    fenced.status_code = 409
    fenced.text = '{"detail": "stale-claim: reassigned"}'
    worker.client.post = AsyncMock(return_value=fenced)

    hung = asyncio.get_running_loop().create_future()

    async def running():
        await hung  # would run forever unless cancelled

    task = asyncio.create_task(running())
    worker._running_workflows["exec-1"] = task

    # Terminal checkpoint: must return promptly (fenced), not wait the deadline.
    ctx = make_ctx(finished=True)
    await asyncio.wait_for(worker._checkpoint(ctx), timeout=5)

    await asyncio.sleep(0.01)
    assert task.cancelled()
    assert "exec-1" not in worker._checkpoint_outboxes
    assert "exec-1" not in worker._claim_generations


@pytest.mark.asyncio
async def test_delta_checkpoints_send_only_unacked_events():
    """The second checkpoint carries only events the server has not acked."""
    worker = make_worker()
    payloads = []

    async def capture_post(url, **kwargs):
        payloads.append(kwargs["json"])
        return ok_response()

    worker.client.post = capture_post

    first = make_ctx()
    first.to_dict.return_value = {
        "execution_id": "exec-1",
        "events": [{"id": "e1"}, {"id": "e2"}],
    }
    await worker._checkpoint(first)
    box = worker._checkpoint_outboxes["exec-1"]
    await asyncio.wait_for(_wait_for_ack(box), timeout=5)

    second = make_ctx(finished=True)
    second.to_dict.return_value = {
        "execution_id": "exec-1",
        "events": [{"id": "e1"}, {"id": "e2"}, {"id": "e3"}, {"id": "e4"}],
    }
    await worker._checkpoint(second)

    assert [e["id"] for e in payloads[0]["events"]] == ["e1", "e2"]
    assert [e["id"] for e in payloads[1]["events"]] == ["e3", "e4"]


@pytest.mark.asyncio
async def test_checkpoint_includes_claim_generation_header():
    worker = make_worker()
    worker._claim_generations["exec-1"] = "7"
    seen_headers = []

    async def capture_post(url, headers=None, **kwargs):
        seen_headers.append(headers or {})
        return ok_response()

    worker.client.post = capture_post

    await worker._checkpoint(make_ctx(finished=True))

    assert seen_headers[0].get("X-Flux-Claim-Generation") == "7"


@pytest.mark.asyncio
async def test_release_claim_is_fenced_even_after_sender_drained():
    """The sender's closed-exit path pops the claim generation; _release_claim
    must capture the fence BEFORE flushing the outbox, or the release goes out
    unfenced and can unclaim a re-dispatched execution that another worker is
    already running."""
    worker = make_worker()
    worker._claim_generations["exec-1"] = "3"
    seen = []

    async def capture_post(url, headers=None, **kwargs):
        seen.append((url, headers or {}))
        return ok_response()

    worker.client.post = capture_post

    # Real outbox with a live sender so the release path drains it (and the
    # sender's closed-exit path pops the generation) before posting.
    await worker._checkpoint(make_ctx())
    box = worker._checkpoint_outboxes["exec-1"]
    await asyncio.wait_for(_wait_for_ack(box), timeout=5)

    await worker._release_claim("exec-1")

    release_calls = [(url, headers) for url, headers in seen if "/release/" in url]
    assert len(release_calls) == 1
    assert release_calls[0][1].get("X-Flux-Claim-Generation") == "3"
    assert "exec-1" not in worker._claim_generations
