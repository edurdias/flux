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
    async with app.run_test() as pilot:
        assert app.query_one("#agent-header") is not None
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
        app.post_message(ToolStarted("read_file", {"path": "/x"}))
        await pilot.pause()
        tools = app.query(ToolBlock)
        assert len(tools) == 1
        assert tools[0]._status is None
        app.post_message(ToolCompleted("read_file", "success"))
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
async def test_session_info_updates_header():
    input_queue: asyncio.Queue[str] = asyncio.Queue()
    app = AgentApp(input_queue=input_queue)
    async with app.run_test() as pilot:
        app.post_message(SessionInfoReceived("exec-abc123def456", "my-agent"))
        await pilot.pause()
        header = app.query_one("#agent-header", Static)
        rendered = str(header.renderable)
        assert "my-agent" in rendered
        assert "exec-abc123d" in rendered
