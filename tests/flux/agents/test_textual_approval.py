"""Tests for TextualUI approval handling.

TextualUI mirrors the elicitation pattern: post a message to the AgentApp,
wait on a future the app resolves when the user presses a/r/A, then return
the decision dict.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
async def test_textual_ui_display_approval_request_returns_approve():
    """TextualUI posts an ApprovalRequested message and resolves the future
    with the operator's decision."""
    from flux.agents.ui.textual_messages import ApprovalRequested
    from flux.agents.ui.textual_ui import TextualUI

    ui = TextualUI()
    posted: list = []
    ui.app = MagicMock()
    ui.app.post_message = posted.append

    request = {
        "execution_id": "exec-1",
        "task_call_id": "deploy_1",
        "task_name": "deploy",
        "workflow_namespace": "default",
        "workflow_name": "release",
    }

    async def _resolve_future_with_approve():
        await asyncio.sleep(0.01)
        msg = posted[0]
        assert isinstance(msg, ApprovalRequested)
        msg.future.set_result({"approved": True, "reason": None})

    asyncio.create_task(_resolve_future_with_approve())
    result = await ui.display_approval_request(request)

    assert result == {"approved": True, "reason": None}
    assert posted[0].request == request


@pytest.mark.asyncio
async def test_textual_ui_display_approval_request_cancelled_returns_defer():
    """If the future is cancelled (app teardown), the call falls back to a
    deferral so the dispatcher does not POST a default decision."""
    from flux.agents.ui.textual_ui import TextualUI

    ui = TextualUI()
    posted: list = []
    ui.app = MagicMock()
    ui.app.post_message = posted.append

    async def _cancel():
        await asyncio.sleep(0.01)
        posted[0].future.cancel()

    asyncio.create_task(_cancel())
    result = await ui.display_approval_request(
        {
            "execution_id": "x",
            "task_call_id": "c",
            "task_name": "t",
            "workflow_namespace": "n",
            "workflow_name": "w",
        },
    )
    assert result == {"defer": True}


@pytest.mark.asyncio
async def test_approval_requested_message_carries_request_and_future():
    """Sanity check: ApprovalRequested holds the request dict and a future
    keyed for the AgentApp's on_approval_requested handler."""
    from flux.agents.ui.textual_messages import ApprovalRequested

    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    msg = ApprovalRequested({"task_name": "deploy"}, fut)
    assert msg.request == {"task_name": "deploy"}
    assert msg.future is fut
