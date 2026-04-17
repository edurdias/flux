"""Tests for worker _handle_execution_resumed claim step."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest


@pytest.mark.asyncio
async def test_handle_execution_resumed_posts_claim_before_executing():
    """Worker must POST /claim before calling _execute_workflow."""
    from flux.worker import Worker

    worker = Worker.__new__(Worker)
    worker.name = "test-worker"
    worker.base_url = "http://localhost:19000/workers"
    worker.session_token = "tok"
    worker.client = AsyncMock()
    worker._running_workflows = {}
    worker._pending_checkpoints = {}
    worker._progress_queues = {}
    worker._progress_flushers = {}
    worker._module_cache = {}
    worker._module_cache_ttl = 0

    calls: list[tuple[str, str]] = []

    claim_response = MagicMock()
    claim_response.raise_for_status = MagicMock()
    claim_response.json = MagicMock(return_value={"state": "RESUME_CLAIMED"})
    claim_response.status_code = 200

    async def mock_post(url, **kwargs):
        calls.append(("post", url))
        return claim_response

    worker.client.post = mock_post

    async def mock_execute(request):
        calls.append(("execute", request.context.execution_id))
        return request.context

    worker._execute_workflow = mock_execute

    evt = MagicMock()
    evt.json.return_value = {
        "workflow": {
            "id": "wf-1",
            "namespace": "default",
            "name": "test_wf",
            "version": 1,
            "source": "",
        },
        "context": {
            "workflow_id": "wf-1",
            "workflow_namespace": "default",
            "workflow_name": "test_wf",
            "execution_id": "exec-resume-1",
            "input": None,
            "state": "RESUME_SCHEDULED",
            "events": [],
        },
    }

    await worker._handle_execution_resumed(evt)

    assert len(calls) >= 2
    assert calls[0][0] == "post"
    assert "claim/exec-resume-1" in calls[0][1]
    assert calls[1] == ("execute", "exec-resume-1")


@pytest.mark.asyncio
async def test_handle_execution_resumed_drops_on_409():
    """409 from /claim means already claimed -- skip _execute_workflow."""
    from flux.worker import Worker

    worker = Worker.__new__(Worker)
    worker.name = "test-worker"
    worker.base_url = "http://localhost:19000/workers"
    worker.session_token = "tok"
    worker.client = AsyncMock()
    worker._running_workflows = {}
    worker._pending_checkpoints = {}
    worker._progress_queues = {}
    worker._progress_flushers = {}
    worker._module_cache = {}
    worker._module_cache_ttl = 0

    async def mock_post(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 409
        resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "conflict",
                request=MagicMock(),
                response=MagicMock(status_code=409),
            ),
        )
        return resp

    worker.client.post = mock_post
    worker._execute_workflow = AsyncMock()

    evt = MagicMock()
    evt.json.return_value = {
        "workflow": {
            "id": "wf-1",
            "namespace": "default",
            "name": "test_wf",
            "version": 1,
            "source": "",
        },
        "context": {
            "workflow_id": "wf-1",
            "workflow_namespace": "default",
            "workflow_name": "test_wf",
            "execution_id": "exec-resume-2",
            "input": None,
            "state": "RESUME_SCHEDULED",
            "events": [],
        },
    }

    await worker._handle_execution_resumed(evt)

    worker._execute_workflow.assert_not_called()
