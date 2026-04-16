"""Tests for AgentSession."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from flux.agents.session import AgentSession


@pytest.mark.asyncio
async def test_start_captures_session_id_and_yields_events():
    client = MagicMock()

    async def fake_start(agent_name, namespace="agents", workflow_name="agent_chat"):
        yield "exec-1", {"execution_id": "exec-1"}
        yield "exec-1", {"type": "task.progress", "value": {"token": "hi"}}
        yield (
            "exec-1",
            {
                "type": "execution.paused",
                "output": {"type": "chat_response", "content": None, "turn": 0},
            },
        )

    client.start_agent = fake_start

    session = AgentSession(client=client, agent_name="coder")

    events = [event async for event in session.start()]

    assert session.session_id == "exec-1"
    kinds = [e.kind for e in events]
    assert "session_id" in kinds
    assert "token" in kinds
    assert "chat_response" in kinds


@pytest.mark.asyncio
async def test_send_requires_session_id():
    client = MagicMock()
    session = AgentSession(client=client, agent_name="coder")

    with pytest.raises(RuntimeError, match="not started"):
        async for _ in session.send("hello"):
            pass


@pytest.mark.asyncio
async def test_send_resumes_with_message():
    client = MagicMock()
    resume_mock = MagicMock()

    async def fake_resume(
        execution_id,
        message=None,
        namespace="agents",
        workflow_name="agent_chat",
        payload=None,
    ):
        resume_mock(execution_id=execution_id, message=message, payload=payload)
        yield {"type": "task.progress", "value": {"token": "ok"}}

    client.resume = fake_resume

    session = AgentSession(client=client, agent_name="coder", session_id="exec-1")

    events = [event async for event in session.send("hello")]

    resume_mock.assert_called_once_with(
        execution_id="exec-1",
        message="hello",
        payload=None,
    )
    assert [e.kind for e in events] == ["token"]


@pytest.mark.asyncio
async def test_respond_to_elicitation_uses_payload():
    client = MagicMock()
    resume_mock = MagicMock()

    async def fake_resume(
        execution_id,
        message=None,
        namespace="agents",
        workflow_name="agent_chat",
        payload=None,
    ):
        resume_mock(execution_id=execution_id, message=message, payload=payload)
        yield {"type": "task.progress", "value": {"token": "done"}}

    client.resume = fake_resume

    session = AgentSession(client=client, agent_name="coder", session_id="exec-1")

    payload = {"elicitation_response": {"elicitation_id": "el-1", "action": "accept"}}
    events = [event async for event in session.respond_to_elicitation(payload)]

    resume_mock.assert_called_once_with(
        execution_id="exec-1",
        message=None,
        payload=payload,
    )
    assert [e.kind for e in events] == ["token"]


@pytest.mark.asyncio
async def test_start_raises_when_session_already_started():
    client = MagicMock()

    async def fake_start(*args, **kwargs):  # noqa: ARG001
        yield "exec-x", {"execution_id": "exec-x"}

    client.start_agent = fake_start

    session = AgentSession(client=client, agent_name="coder", session_id="exec-1")

    with pytest.raises(RuntimeError, match="already started"):
        async for _ in session.start():
            pass
