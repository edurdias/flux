"""Worker side of dispatch-frame fencing.

The worker echoes the generation a dispatch frame was built under, so the
server can refuse a frame superseded by a release or re-dispatch. A refused
frame is a duplicate of work this worker may already be running, and its
handler must leave that run's checkpoint outbox alone.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _worker():
    from flux.worker import Worker

    worker = Worker.__new__(Worker)
    worker.name = "w1"
    worker.base_url = "http://localhost:19000/workers"
    worker.session_token = "tok"
    worker.client = AsyncMock()
    worker._running_workflows = {}
    worker._checkpoint_outboxes = {}
    worker._claim_generations = {}
    worker._claiming = set()
    worker._registered = True
    worker._reauth_lock = asyncio.Lock()
    worker._checkpoint_retry_max_delay = 30
    worker._terminal_checkpoint_deadline = 300
    worker._progress_queues = {}
    worker._progress_flushers = {}
    worker._progress_channels = {}
    worker._runners = {}
    worker._default_runner = "subprocess"
    worker._healthy = True
    worker._paused = False
    worker._draining = False
    worker._execute_workflow = AsyncMock(side_effect=lambda request: request.context)
    return worker


def _frame(state: str, generation: int | None):
    data = {
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
            "execution_id": "exec-1",
            "input": None,
            "state": state,
            "events": [],
        },
    }
    if generation is not None:
        data["claim_generation"] = generation
    evt = MagicMock()
    evt.json.return_value = data
    return evt


def _claim_response(status: int, state: str = "CLAIMED"):
    import httpx

    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"X-Flux-Claim-Generation": "7"} if status == 200 else {}
    resp.json = MagicMock(
        return_value={
            "workflow_id": "wf-1",
            "workflow_namespace": "default",
            "workflow_name": "wf",
            "execution_id": "exec-1",
            "input": None,
            "state": state,
            "events": [],
        },
    )
    if status >= 400:
        resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "conflict",
                request=MagicMock(),
                response=MagicMock(status_code=status),
            ),
        )
    else:
        resp.raise_for_status = MagicMock()
    return resp


async def _dispatch(worker, kind: str, generation: int | None):
    if kind == "scheduled":
        await worker._handle_execution_scheduled(
            f"{worker.base_url}/{worker.name}",
            _frame("SCHEDULED", generation),
        )
    else:
        await worker._handle_execution_resumed(_frame("RESUME_SCHEDULED", generation))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["scheduled", "resumed"])
async def test_claim_echoes_the_frames_generation(kind):
    worker = _worker()
    sent = {}

    async def post(url, **kwargs):
        sent.update(kwargs.get("headers") or {})
        return _claim_response(200)

    worker.client.post = post

    await _dispatch(worker, kind, generation=7)

    assert sent["X-Flux-Claim-Generation"] == "7"
    worker._execute_workflow.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["scheduled", "resumed"])
async def test_frame_from_a_pre_fencing_server_claims_unfenced(kind):
    worker = _worker()
    sent = {}

    async def post(url, **kwargs):
        sent.update(kwargs.get("headers") or {})
        return _claim_response(200)

    worker.client.post = post

    await _dispatch(worker, kind, generation=None)

    assert "X-Flux-Claim-Generation" not in sent
    worker._execute_workflow.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["scheduled", "resumed"])
async def test_refused_frame_leaves_the_live_runs_outbox_open(kind):
    """The old frame's claim lands after the current frame's claim already
    started the run: its 409 must not close that run's outbox."""
    worker = _worker()
    live = SimpleNamespace(closed=False, wakeup=asyncio.Event())
    worker._checkpoint_outboxes["exec-1"] = live

    async def post(url, **kwargs):
        return _claim_response(409)

    worker.client.post = post

    await _dispatch(worker, kind, generation=1)

    assert live.closed is False
    assert worker._checkpoint_outboxes["exec-1"] is live
    worker._execute_workflow.assert_not_awaited()
