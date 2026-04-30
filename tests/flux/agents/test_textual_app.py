"""Tests for AgentApp Textual application."""

from __future__ import annotations

import asyncio

import pytest

from textual.widgets import Static

from flux.agents.ui.textual_app import AgentApp
from flux.agents.ui.textual_messages import (
    ReasoningReceived,
    ReplyEnded,
    ReplyStarted,
    ResponseReceived,
    SessionInfoReceived,
    TokenReceived,
    ToolCompleted,
    ToolStarted,
)
from flux.agents.ui.textual_widgets import StreamBlock, ThinkingBlock, ToolBlock


@pytest.mark.asyncio
async def test_app_composes_widgets():
    input_queue: asyncio.Queue[str] = asyncio.Queue()
    app = AgentApp(input_queue=input_queue)
    async with app.run_test():
        assert app.query_one("#chat-view") is not None
        assert app.query_one("#agent-input") is not None
        assert app.query_one("#status-bar") is not None


@pytest.mark.asyncio
async def test_token_streaming_creates_stream_block():
    input_queue: asyncio.Queue[str] = asyncio.Queue()
    app = AgentApp(input_queue=input_queue)
    async with app.run_test() as pilot:
        app.post_message(ReplyStarted())
        await pilot.pause()
        app.post_message(TokenReceived("Hello "))
        await pilot.pause()
        app.post_message(TokenReceived("world"))
        await pilot.pause()
        blocks = app.query(StreamBlock)
        assert len(blocks) == 1
        assert blocks[0].buffer == "Hello world"


@pytest.mark.asyncio
async def test_response_finalizes_stream():
    input_queue: asyncio.Queue[str] = asyncio.Queue()
    app = AgentApp(input_queue=input_queue)
    async with app.run_test() as pilot:
        app.post_message(ReplyStarted())
        await pilot.pause()
        app.post_message(TokenReceived("Hello"))
        await pilot.pause()
        app.post_message(ResponseReceived("# Hello\n\nFormatted."))
        await pilot.pause()
        app.post_message(ReplyEnded())
        await pilot.pause()
        blocks = app.query(StreamBlock)
        assert len(blocks) == 1
        md = blocks[0].query("Markdown")
        assert len(md) == 1


@pytest.mark.asyncio
async def test_tool_start_and_done():
    input_queue: asyncio.Queue[str] = asyncio.Queue()
    app = AgentApp(input_queue=input_queue)
    async with app.run_test() as pilot:
        app.post_message(ReplyStarted())
        await pilot.pause()
        app.post_message(ToolStarted("call_1", "read_file", {"path": "/x"}))
        await pilot.pause()
        tools = app.query(ToolBlock)
        assert len(tools) == 1
        assert tools[0]._status is None
        app.post_message(ToolCompleted("call_1", "read_file", "success"))
        await pilot.pause()
        assert tools[0]._status == "success"


@pytest.mark.asyncio
async def test_reasoning_creates_thinking_block():
    input_queue: asyncio.Queue[str] = asyncio.Queue()
    app = AgentApp(input_queue=input_queue)
    async with app.run_test() as pilot:
        app.post_message(ReplyStarted())
        await pilot.pause()
        app.post_message(ReasoningReceived("Let me think..."))
        await pilot.pause()
        blocks = app.query(ThinkingBlock)
        assert len(blocks) == 1
        assert blocks[0].line_count == 1


@pytest.mark.asyncio
async def test_session_info_updates_status_bar():
    input_queue: asyncio.Queue[str] = asyncio.Queue()
    app = AgentApp(input_queue=input_queue)
    async with app.run_test() as pilot:
        app.post_message(SessionInfoReceived("exec-abc123def456", "my-agent"))
        await pilot.pause()
        status = app.query_one("#status-bar", Static)
        rendered = str(status.renderable)
        assert "my-agent" in rendered
        assert "exec-abc123def456" in rendered


@pytest.mark.asyncio
async def test_spinner_starts_on_reply():
    input_queue: asyncio.Queue[str] = asyncio.Queue()
    app = AgentApp(input_queue=input_queue)
    async with app.run_test() as pilot:
        app.post_message(ReplyStarted())
        await pilot.pause()
        assert app._is_processing is True
        assert app._spinner is not None


@pytest.mark.asyncio
async def test_spinner_stops_on_reply_end():
    input_queue: asyncio.Queue[str] = asyncio.Queue()
    app = AgentApp(input_queue=input_queue)
    async with app.run_test() as pilot:
        app.post_message(ReplyStarted())
        await pilot.pause()
        app.post_message(ReplyEnded())
        await pilot.pause()
        assert app._is_processing is False
        assert app._spinner is None


@pytest.mark.asyncio
async def test_input_never_disabled_during_processing():
    from textual.widgets import Input

    input_queue: asyncio.Queue[str] = asyncio.Queue()
    app = AgentApp(input_queue=input_queue)
    async with app.run_test() as pilot:
        app.post_message(ReplyStarted())
        await pilot.pause()
        input_widget = app.query_one("#agent-input", Input)
        assert input_widget.disabled is False
        app.post_message(TokenReceived("Hello"))
        await pilot.pause()
        assert input_widget.disabled is False
