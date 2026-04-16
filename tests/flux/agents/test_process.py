"""Tests for AgentProcess."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flux.agents.events import AgentEvent
from flux.agents.process import AgentProcess


def test_process_init():
    process = AgentProcess(
        agent_name="coder",
        server_url="http://localhost:8000",
        mode="terminal",
    )
    assert process.agent_name == "coder"
    assert process.mode == "terminal"


def test_process_invalid_mode():
    with pytest.raises(ValueError, match="mode"):
        AgentProcess(
            agent_name="coder",
            server_url="http://localhost:8000",
            mode="invalid",
        )


def test_process_with_session():
    process = AgentProcess(
        agent_name="coder",
        server_url="http://localhost:8000",
        mode="terminal",
        session_id="exec_123",
    )
    assert process.session_id == "exec_123"


@pytest.mark.asyncio
async def test_terminal_mode_dispatches_events_to_ui():
    proc = AgentProcess(agent_name="coder", server_url="http://x", mode="terminal")
    proc.ui = MagicMock()
    proc.ui.display_session_info = AsyncMock()
    proc.ui.display_response = AsyncMock()
    proc.ui.display_token = AsyncMock()
    proc.ui.display_tool_start = AsyncMock()
    proc.ui.display_tool_done = AsyncMock()
    proc.ui.display_elicitation = AsyncMock(return_value={})
    proc.ui.prompt_user = AsyncMock(side_effect=["/quit"])

    async def fake_session_start():
        yield AgentEvent(kind="session_id", data={"id": "exec-1"})
        yield AgentEvent(kind="token", data={"text": "hello"})
        yield AgentEvent(
            kind="chat_response",
            data={"content": None, "turn": 0},
        )

    fake_session = MagicMock()
    fake_session.start = fake_session_start
    fake_session.session_id = "exec-1"

    with patch("flux.agents.process.AgentSession", return_value=fake_session):
        await proc.run()

    proc.ui.display_token.assert_any_call("hello")
    proc.ui.display_response.assert_any_call(None)


@pytest.mark.asyncio
async def test_terminal_mode_dispatches_session_end():
    proc = AgentProcess(agent_name="coder", server_url="http://x", mode="terminal")
    proc.ui = MagicMock()
    proc.ui.display_session_info = AsyncMock()
    proc.ui.display_session_end = AsyncMock()
    proc.ui.prompt_user = AsyncMock(side_effect=["/quit"])

    async def fake_session_start():
        yield AgentEvent(kind="session_id", data={"id": "exec-1"})
        yield AgentEvent(
            kind="session_end",
            data={"reason": "max_turns", "turns": 5},
        )

    fake_session = MagicMock()
    fake_session.start = fake_session_start
    fake_session.session_id = "exec-1"

    with patch("flux.agents.process.AgentSession", return_value=fake_session):
        await proc.run()

    proc.ui.display_session_end.assert_called_once()
    call_arg = proc.ui.display_session_end.call_args.args[0]
    assert call_arg.reason == "max_turns"
    assert call_arg.turns == 5
