"""Tests for custom Textual messages."""

from __future__ import annotations

from flux.agents.ui.textual_messages import (
    ElicitationRequested,
    ReasoningReceived,
    ReplyEnded,
    ReplyStarted,
    ResponseReceived,
    SessionEnded,
    SessionInfoReceived,
    TokenReceived,
    ToolCompleted,
    ToolStarted,
)


def test_token_received():
    msg = TokenReceived("hello")
    assert msg.text == "hello"


def test_tool_started():
    msg = ToolStarted("read_file", {"path": "/tmp"})
    assert msg.name == "read_file"
    assert msg.args == {"path": "/tmp"}


def test_tool_completed():
    msg = ToolCompleted("read_file", "success")
    assert msg.name == "read_file"
    assert msg.status == "success"


def test_reasoning_received():
    msg = ReasoningReceived("thinking about it")
    assert msg.text == "thinking about it"


def test_response_received():
    msg = ResponseReceived("final answer")
    assert msg.content == "final answer"


def test_response_received_none():
    msg = ResponseReceived(None)
    assert msg.content is None


def test_reply_started():
    msg = ReplyStarted()
    assert isinstance(msg, ReplyStarted)


def test_reply_ended():
    msg = ReplyEnded()
    assert isinstance(msg, ReplyEnded)


def test_session_info_received():
    msg = SessionInfoReceived("exec-123", "coder")
    assert msg.session_id == "exec-123"
    assert msg.agent_name == "coder"


def test_session_ended():
    msg = SessionEnded("max_turns", 5)
    assert msg.reason == "max_turns"
    assert msg.turns == 5


def test_elicitation_requested():
    import asyncio

    loop = asyncio.new_event_loop()
    future = loop.create_future()
    req = {"url": "https://example.com", "message": "auth"}
    msg = ElicitationRequested(req, future)
    assert msg.request == req
    assert msg.future is future
    future.cancel()
    loop.close()
