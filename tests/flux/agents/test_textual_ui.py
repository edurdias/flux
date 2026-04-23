"""Tests for TextualUI bridge."""

from __future__ import annotations


import pytest

from flux.agents.ui.textual_ui import TextualUI


def test_textual_ui_creates_app():
    ui = TextualUI()
    assert ui.app is not None
    assert ui._input_queue is not None


@pytest.mark.asyncio
async def test_prompt_user_returns_from_queue():
    ui = TextualUI()
    ui._input_queue.put_nowait("hello")
    result = await ui.prompt_user()
    assert result == "hello"


@pytest.mark.asyncio
async def test_display_token_posts_message():
    ui = TextualUI()
    messages = []
    ui.app.post_message = lambda msg: messages.append(msg) or True
    await ui.display_token("hi")
    assert len(messages) == 1
    assert messages[0].text == "hi"


@pytest.mark.asyncio
async def test_display_tool_start_posts_message():
    ui = TextualUI()
    messages = []
    ui.app.post_message = lambda msg: messages.append(msg) or True
    await ui.display_tool_start("call_1", "read_file", {"path": "/x"})
    assert len(messages) == 1
    assert messages[0].tool_id == "call_1"
    assert messages[0].name == "read_file"


@pytest.mark.asyncio
async def test_display_tool_done_posts_message():
    ui = TextualUI()
    messages = []
    ui.app.post_message = lambda msg: messages.append(msg) or True
    await ui.display_tool_done("call_1", "read_file", "success")
    assert len(messages) == 1
    assert messages[0].tool_id == "call_1"
    assert messages[0].name == "read_file"
    assert messages[0].status == "success"


@pytest.mark.asyncio
async def test_display_reasoning_posts_message():
    ui = TextualUI()
    messages = []
    ui.app.post_message = lambda msg: messages.append(msg) or True
    await ui.display_reasoning("hmm")
    assert len(messages) == 1
    assert messages[0].text == "hmm"


@pytest.mark.asyncio
async def test_display_response_posts_message():
    ui = TextualUI()
    messages = []
    ui.app.post_message = lambda msg: messages.append(msg) or True
    await ui.display_response("done")
    assert len(messages) == 1
    assert messages[0].content == "done"


@pytest.mark.asyncio
async def test_begin_end_reply_posts_messages():
    ui = TextualUI()
    messages = []
    ui.app.post_message = lambda msg: messages.append(msg) or True
    await ui.begin_reply()
    await ui.end_reply()
    assert len(messages) == 2


@pytest.mark.asyncio
async def test_display_session_info_posts_message():
    ui = TextualUI()
    messages = []
    ui.app.post_message = lambda msg: messages.append(msg) or True
    await ui.display_session_info("exec-1", "agent")
    assert len(messages) == 1
    assert messages[0].session_id == "exec-1"
