"""Tests for streaming progress events during tool execution."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flux.tasks.ai.agent_loop import run_agent_loop
from flux.tasks.ai.models import LLMResponse, ToolCall


@pytest.fixture
def mock_formatter():
    formatter = MagicMock()
    formatter.build_messages.return_value = ([], {})
    formatter.format_assistant_message.return_value = {"role": "assistant", "content": ""}
    formatter.format_tool_results.return_value = [{"role": "tool", "content": "result"}]
    formatter.format_user_message.return_value = {"role": "user", "content": ""}
    formatter.remove_tools_from_kwargs.return_value = {}
    return formatter


@pytest.fixture
def progress_events():
    events = []

    async def mock_progress(value):
        events.append(value)

    return events, mock_progress


def _make_llm_task(responses):
    call_count = 0

    async def mock_llm(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return responses[min(call_count - 1, len(responses) - 1)]

    llm_task = MagicMock()
    llm_task.with_options.return_value = MagicMock(side_effect=mock_llm)
    return llm_task


def test_tool_start_progress_event_emitted(mock_formatter, progress_events):
    events, mock_progress = progress_events

    tool_call = ToolCall(id="tc_1", name="search_web", arguments={"query": "weather"})
    response_with_tools = LLMResponse(text="Let me search.", tool_calls=[tool_call])
    response_final = LLMResponse(text="The weather is sunny.")

    llm_task = _make_llm_task([response_with_tools, response_final])
    tool_schemas = [{"name": "search_web", "parameters": {"query": {"type": "string"}}}]

    mock_tool = AsyncMock(return_value="sunny")
    mock_tool.name = "search_web"
    mock_tool.func = MagicMock(__name__="search_web")
    mock_tool.requires_approval = False

    with patch("flux.tasks.ai.agent_loop.progress", mock_progress):
        with patch("flux.tasks.ai.agent_loop.execute_tools", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = [{"tool_call_id": "tc_1", "output": "sunny"}]

            asyncio.get_event_loop().run_until_complete(
                run_agent_loop(
                    llm_task=llm_task,
                    formatter=mock_formatter,
                    system_prompt="You are helpful.",
                    instruction="What is the weather?",
                    tools=[mock_tool],
                    tool_schemas=tool_schemas,
                    stream=True,
                ),
            )

    tool_start_events = [e for e in events if e.get("type") == "tool_start"]
    assert len(tool_start_events) == 1
    assert tool_start_events[0]["name"] == "search_web"
    assert tool_start_events[0]["args"] == {"query": "weather"}


def test_tool_done_progress_event_emitted(mock_formatter, progress_events):
    events, mock_progress = progress_events

    tool_call = ToolCall(id="tc_1", name="read_file", arguments={"path": "/tmp/test"})
    response_with_tools = LLMResponse(text="Reading file.", tool_calls=[tool_call])
    response_final = LLMResponse(text="File contents are: hello")

    llm_task = _make_llm_task([response_with_tools, response_final])
    tool_schemas = [{"name": "read_file", "parameters": {"path": {"type": "string"}}}]

    mock_tool = AsyncMock(return_value="hello")
    mock_tool.name = "read_file"
    mock_tool.func = MagicMock(__name__="read_file")
    mock_tool.requires_approval = False

    with patch("flux.tasks.ai.agent_loop.progress", mock_progress):
        with patch("flux.tasks.ai.agent_loop.execute_tools", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = [{"tool_call_id": "tc_1", "output": "hello"}]

            asyncio.get_event_loop().run_until_complete(
                run_agent_loop(
                    llm_task=llm_task,
                    formatter=mock_formatter,
                    system_prompt="You are helpful.",
                    instruction="Read the file.",
                    tools=[mock_tool],
                    tool_schemas=tool_schemas,
                    stream=True,
                ),
            )

    tool_done_events = [e for e in events if e.get("type") == "tool_done"]
    assert len(tool_done_events) == 1
    assert tool_done_events[0]["name"] == "read_file"
    assert tool_done_events[0]["status"] == "success"


def test_tool_done_error_status(mock_formatter, progress_events):
    events, mock_progress = progress_events

    tool_call = ToolCall(id="tc_1", name="shell", arguments={"cmd": "false"})
    response_with_tools = LLMResponse(text="Running.", tool_calls=[tool_call])
    response_final = LLMResponse(text="Command failed.")

    llm_task = _make_llm_task([response_with_tools, response_final])
    tool_schemas = [{"name": "shell", "parameters": {"cmd": {"type": "string"}}}]

    mock_tool = AsyncMock(return_value="error")
    mock_tool.name = "shell"
    mock_tool.func = MagicMock(__name__="shell")
    mock_tool.requires_approval = False

    with patch("flux.tasks.ai.agent_loop.progress", mock_progress):
        with patch("flux.tasks.ai.agent_loop.execute_tools", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = [
                {"tool_call_id": "tc_1", "output": "", "error": "exit code 1"},
            ]

            asyncio.get_event_loop().run_until_complete(
                run_agent_loop(
                    llm_task=llm_task,
                    formatter=mock_formatter,
                    system_prompt="You are helpful.",
                    instruction="Run false.",
                    tools=[mock_tool],
                    tool_schemas=tool_schemas,
                    stream=True,
                ),
            )

    tool_done_events = [e for e in events if e.get("type") == "tool_done"]
    assert len(tool_done_events) == 1
    assert tool_done_events[0]["status"] == "error"


def test_progress_events_order(mock_formatter, progress_events):
    events, mock_progress = progress_events

    tool_call = ToolCall(id="tc_1", name="search_web", arguments={"q": "test"})
    response_with_tools = LLMResponse(text="", tool_calls=[tool_call])
    response_final = LLMResponse(text="Done.")

    llm_task = _make_llm_task([response_with_tools, response_final])

    mock_tool = AsyncMock(return_value="ok")
    mock_tool.name = "search_web"
    mock_tool.func = MagicMock(__name__="search_web")
    mock_tool.requires_approval = False

    with patch("flux.tasks.ai.agent_loop.progress", mock_progress):
        with patch("flux.tasks.ai.agent_loop.execute_tools", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = [{"tool_call_id": "tc_1", "output": "ok"}]

            asyncio.get_event_loop().run_until_complete(
                run_agent_loop(
                    llm_task=llm_task,
                    formatter=mock_formatter,
                    system_prompt="Help.",
                    instruction="Search.",
                    tools=[mock_tool],
                    tool_schemas=[{"name": "search_web", "parameters": {}}],
                    stream=True,
                ),
            )

    types = [e["type"] for e in events if e.get("type") in ("tool_start", "tool_done")]
    assert types == ["tool_start", "tool_done"]
