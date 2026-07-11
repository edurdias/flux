"""Tests for the approval_required event path:
   parse_event -> AgentEvent -> process._dispatch -> UI -> FluxClient.decide_approval.

Tasks 19-20 wire engine-level ``requires_approval`` pauses into the agent
harness UIs. The dispatcher in flux/agents/process.py is the integration
point: it asks the UI for a decision, then POSTs that decision back to the
Flux server via FluxClient.decide_approval.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from flux.agents.events import AgentEvent
from flux.agents.process import AgentProcess


def _approval_event(task_name: str = "deploy", call_id: str = "deploy_1") -> AgentEvent:
    return AgentEvent(
        kind="approval_required",
        data={
            "execution_id": "exec-1",
            "task_call_id": call_id,
            "task_name": task_name,
            "workflow_namespace": "default",
            "workflow_name": "release",
            "approval_id": "appr-1",
            "requested_at": "2026-05-07T10:00:00",
        },
    )


def _make_process_with_mock_ui() -> AgentProcess:
    proc = AgentProcess(
        agent_name="coder",
        server_url="http://x",
        mode="terminal",
    )
    proc.ui = MagicMock()
    mock_client = MagicMock()
    mock_client.decide_approval = AsyncMock(return_value={"status": "approved"})
    proc.client = mock_client
    return proc


async def _aiter(items=()):
    for item in items:
        yield item


def _make_session(reattach_events=()) -> MagicMock:
    """A session mock whose reattach() yields the given AgentEvents.

    _handle_approval_request re-attaches to the execution stream after a
    decision, so the session must expose an async-iterating reattach().
    """
    session = MagicMock()
    session.reattach = lambda: _aiter(reattach_events)
    return session


@pytest.mark.asyncio
async def test_dispatch_approval_request_calls_ui_then_posts_approve():
    proc = _make_process_with_mock_ui()
    proc.ui.display_approval_request = AsyncMock(
        return_value={"approved": True, "reason": "lgtm"},
    )

    session = _make_session()
    await proc._dispatch(_approval_event(), session)

    proc.ui.display_approval_request.assert_awaited_once()
    proc.client.decide_approval.assert_awaited_once_with(
        "exec-1",
        "deploy_1",
        approved=True,
        reason="lgtm",
        always=False,
    )


@pytest.mark.asyncio
async def test_dispatch_approval_request_posts_reject_when_ui_returns_false():
    proc = _make_process_with_mock_ui()
    proc.ui.display_approval_request = AsyncMock(
        return_value={"approved": False, "reason": "no good"},
    )

    session = _make_session()
    await proc._dispatch(_approval_event(), session)

    proc.client.decide_approval.assert_awaited_once_with(
        "exec-1",
        "deploy_1",
        approved=False,
        reason="no good",
        always=False,
    )


@pytest.mark.asyncio
async def test_dispatch_approval_request_threads_always_flag():
    """A UI answering with ``always: True`` (the ``[A]`` key) must reach the
    server as a standing grant (issue #74)."""
    proc = _make_process_with_mock_ui()
    proc.ui.display_approval_request = AsyncMock(
        return_value={"approved": True, "reason": None, "always": True},
    )

    session = _make_session()
    await proc._dispatch(_approval_event(), session)

    proc.client.decide_approval.assert_awaited_once_with(
        "exec-1",
        "deploy_1",
        approved=True,
        reason=None,
        always=True,
    )


@pytest.mark.asyncio
async def test_dispatch_approval_reattaches_to_stream_after_decision():
    """After POSTing a decision the dispatcher re-attaches to the execution
    stream and dispatches the events produced once the workflow resumes."""
    proc = _make_process_with_mock_ui()
    proc.ui.display_approval_request = AsyncMock(
        return_value={"approved": True, "reason": None},
    )
    proc.ui.display_response = AsyncMock()

    resumed = AgentEvent(kind="chat_response", data={"content": "resumed output"})
    session = _make_session(reattach_events=[resumed])
    await proc._dispatch(_approval_event(), session)

    proc.client.decide_approval.assert_awaited_once()
    # The event produced after the resume reached the UI.
    proc.ui.display_response.assert_awaited_once_with("resumed output")


@pytest.mark.asyncio
async def test_dispatch_approval_request_skips_post_when_ui_defers():
    """A UI that returns ``{'defer': True}`` (api/web modes) means the
    decision will be made via another channel; the dispatcher must not POST."""
    proc = _make_process_with_mock_ui()
    proc.ui.display_approval_request = AsyncMock(return_value={"defer": True})

    session = _make_session()
    await proc._dispatch(_approval_event(), session)

    proc.client.decide_approval.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_approval_request_default_ui_method_defers():
    """The UI ABC's default display_approval_request should defer (no decision),
    so api/web UIs that don't override it don't accidentally auto-approve."""
    from flux.agents.ui import UI

    class _MinimalUI(UI):
        async def display_response(self, content): ...
        async def display_tool_start(self, tool_id, name, args): ...
        async def display_tool_done(self, tool_id, name, status): ...
        async def display_token(self, text): ...
        async def display_reasoning(self, text): ...
        async def display_elicitation(self, request): ...
        async def prompt_user(self): ...
        async def display_session_info(self, session_id, agent_name): ...
        async def display_session_end(self, output): ...

    ui = _MinimalUI()
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
async def test_terminal_ui_display_approval_request_returns_approve(monkeypatch, capsys):
    from flux.agents.ui.terminal import TerminalUI

    monkeypatch.setattr("builtins.input", lambda *_: "a")
    ui = TerminalUI()
    result = await ui.display_approval_request(
        {
            "execution_id": "exec-1",
            "task_call_id": "deploy_1",
            "task_name": "deploy",
            "workflow_namespace": "default",
            "workflow_name": "release",
        },
    )
    assert result == {"approved": True, "reason": None}
    out = capsys.readouterr().out
    assert "deploy" in out
    assert "approval" in out.lower()


@pytest.mark.asyncio
async def test_terminal_ui_display_approval_request_returns_reject_with_reason(
    monkeypatch,
):
    from flux.agents.ui.terminal import TerminalUI

    answers = iter(["r", "too risky"])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    ui = TerminalUI()
    result = await ui.display_approval_request(
        {
            "execution_id": "x",
            "task_call_id": "c",
            "task_name": "deploy",
            "workflow_namespace": "n",
            "workflow_name": "w",
        },
    )
    assert result == {"approved": False, "reason": "too risky"}


@pytest.mark.asyncio
async def test_terminal_ui_display_approval_request_capital_a_is_standing_grant(monkeypatch):
    """Capital 'A' approves with ``always: True`` — the engine-managed
    standing grant (issue #74), so the same task won't prompt again."""
    from flux.agents.ui.terminal import TerminalUI

    monkeypatch.setattr("builtins.input", lambda *_: "A")
    ui = TerminalUI()
    result = await ui.display_approval_request(
        {
            "execution_id": "x",
            "task_call_id": "c",
            "task_name": "deploy",
            "workflow_namespace": "n",
            "workflow_name": "w",
        },
    )
    assert result == {"approved": True, "reason": None, "always": True}
