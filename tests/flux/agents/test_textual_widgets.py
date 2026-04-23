"""Tests for custom Textual widgets."""

from __future__ import annotations

import asyncio

import pytest

from textual.app import App, ComposeResult
from textual.widgets import Static

from flux.agents.ui.textual_widgets import (
    ElicitationPrompt,
    StreamBlock,
    ThinkingBlock,
    ToolBlock,
)


class StreamBlockApp(App):
    def compose(self) -> ComposeResult:
        yield StreamBlock()


@pytest.mark.asyncio
async def test_stream_block_append_and_finalize():
    async with StreamBlockApp().run_test() as pilot:
        block = pilot.app.query_one(StreamBlock)
        block.append_token("Hello")
        block.append_token(" world")
        assert block.buffer == "Hello world"
        block.finalize("# Hello world\n\nFinal content.")
        await pilot.pause()
        md = block.query_one("Markdown")
        assert md is not None


@pytest.mark.asyncio
async def test_stream_block_handles_blob():
    async with StreamBlockApp().run_test() as pilot:
        block = pilot.app.query_one(StreamBlock)
        block.append_token("This is the entire response in one shot.")
        assert "entire response" in block.buffer


class ToolBlockApp(App):
    def compose(self) -> ComposeResult:
        yield ToolBlock("read_file", {"path": "/tmp/x.py"})


@pytest.mark.asyncio
async def test_tool_block_pending():
    async with ToolBlockApp().run_test() as pilot:
        block = pilot.app.query_one(ToolBlock)
        content = block.render_text()
        assert "read_file" in content
        assert "/tmp/x.py" in content


@pytest.mark.asyncio
async def test_tool_block_success():
    async with ToolBlockApp().run_test() as pilot:
        block = pilot.app.query_one(ToolBlock)
        block.mark_done("success")
        content = block.render_text()
        assert "\u2713" in content


@pytest.mark.asyncio
async def test_tool_block_error():
    async with ToolBlockApp().run_test() as pilot:
        block = pilot.app.query_one(ToolBlock)
        block.mark_done("error")
        content = block.render_text()
        assert "\u2717" in content


class ThinkingBlockApp(App):
    def compose(self) -> ComposeResult:
        yield ThinkingBlock()


@pytest.mark.asyncio
async def test_thinking_block_append():
    async with ThinkingBlockApp().run_test() as pilot:
        block = pilot.app.query_one(ThinkingBlock)
        block.append_text("line 1\nline 2")
        assert block.line_count == 2
        assert block.collapsed is True


@pytest.mark.asyncio
async def test_thinking_block_finalize():
    async with ThinkingBlockApp().run_test() as pilot:
        block = pilot.app.query_one(ThinkingBlock)
        block.append_text("line 1\nline 2\nline 3")
        block.finalize()
        assert "3 lines" in block.title


class ElicitationApp(App):
    def __init__(self):
        super().__init__()
        self.prompt_widget = ElicitationPrompt(
            server_name="mcp-github",
            message="Authorization required",
        )

    def compose(self) -> ComposeResult:
        yield self.prompt_widget


@pytest.mark.asyncio
async def test_elicitation_prompt_renders():
    async with ElicitationApp().run_test() as pilot:
        prompt = pilot.app.query_one(ElicitationPrompt)
        text = prompt.render_text()
        assert "mcp-github" in text
        assert "Authorization required" in text
        assert "[Y]" in text or "[N]" in text
