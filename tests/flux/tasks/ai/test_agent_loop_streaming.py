"""Tests for streaming progress events during tool execution."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flux.tasks.ai.agent_loop import run_agent_loop
from flux.tasks.ai.models import LLMResponse, ReasoningContent, ToolCall


@pytest.fixture
def mock_formatter():
    formatter = MagicMock()
    formatter.build_messages.return_value = ([], {})
    formatter.format_assistant_message.return_value = {"role": "assistant", "content": ""}
    formatter.format_tool_results.return_value = [{"role": "tool", "content": "result"}]
    formatter.format_user_message.return_value = {"role": "user", "content": ""}
    formatter.remove_tools_from_kwargs.return_value = {}
    formatter.supports_reasoning_stream = False
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


@pytest.mark.asyncio
async def test_tool_start_progress_event_emitted(mock_formatter, progress_events):
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

            await run_agent_loop(
                llm_task=llm_task,
                formatter=mock_formatter,
                system_prompt="You are helpful.",
                instruction="What is the weather?",
                tools=[mock_tool],
                tool_schemas=tool_schemas,
                stream=True,
            )

    tool_start_events = [e for e in events if e.get("type") == "tool_start"]
    assert len(tool_start_events) == 1
    assert tool_start_events[0]["name"] == "search_web"
    assert tool_start_events[0]["args"] == {"query": "weather"}


@pytest.mark.asyncio
async def test_tool_done_progress_event_emitted(mock_formatter, progress_events):
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

            await run_agent_loop(
                llm_task=llm_task,
                formatter=mock_formatter,
                system_prompt="You are helpful.",
                instruction="Read the file.",
                tools=[mock_tool],
                tool_schemas=tool_schemas,
                stream=True,
            )

    tool_done_events = [e for e in events if e.get("type") == "tool_done"]
    assert len(tool_done_events) == 1
    assert tool_done_events[0]["name"] == "read_file"
    assert tool_done_events[0]["status"] == "success"


@pytest.mark.asyncio
async def test_tool_done_error_status(mock_formatter, progress_events):
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

            await run_agent_loop(
                llm_task=llm_task,
                formatter=mock_formatter,
                system_prompt="You are helpful.",
                instruction="Run false.",
                tools=[mock_tool],
                tool_schemas=tool_schemas,
                stream=True,
            )

    tool_done_events = [e for e in events if e.get("type") == "tool_done"]
    assert len(tool_done_events) == 1
    assert tool_done_events[0]["status"] == "error"


@pytest.mark.asyncio
async def test_progress_events_order(mock_formatter, progress_events):
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

            await run_agent_loop(
                llm_task=llm_task,
                formatter=mock_formatter,
                system_prompt="Help.",
                instruction="Search.",
                tools=[mock_tool],
                tool_schemas=[{"name": "search_web", "parameters": {}}],
                stream=True,
            )

    types = [e["type"] for e in events if e.get("type") in ("tool_start", "tool_done")]
    assert types == ["tool_start", "tool_done"]


@pytest.mark.asyncio
async def test_reasoning_progress_event_emitted(mock_formatter, progress_events):
    """When LLM returns reasoning content, a reasoning progress event is emitted."""
    events, mock_progress = progress_events

    reasoning = ReasoningContent(text="Let me think about this step by step.")
    response_with_reasoning = LLMResponse(
        text="The answer is 42.",
        reasoning=reasoning,
    )

    llm_task = _make_llm_task([response_with_reasoning])

    mock_tool = AsyncMock(return_value="ok")
    mock_tool.name = "dummy"
    mock_tool.func = MagicMock(__name__="dummy")
    mock_tool.requires_approval = False

    with patch("flux.tasks.ai.agent_loop.progress", mock_progress):
        await run_agent_loop(
            llm_task=llm_task,
            formatter=mock_formatter,
            system_prompt="You are helpful.",
            instruction="What is the meaning of life?",
            tools=[mock_tool],
            tool_schemas=[{"name": "dummy", "parameters": {}}],
            stream=True,
        )

    reasoning_events = [e for e in events if e.get("type") == "reasoning"]
    assert len(reasoning_events) == 1
    assert reasoning_events[0]["text"] == "Let me think about this step by step."


@pytest.mark.asyncio
async def test_no_reasoning_event_when_reasoning_is_none(mock_formatter, progress_events):
    """No reasoning event when the LLM response has no reasoning."""
    events, mock_progress = progress_events

    response_no_reasoning = LLMResponse(text="Simple answer.")

    llm_task = _make_llm_task([response_no_reasoning])

    mock_tool = AsyncMock(return_value="ok")
    mock_tool.name = "dummy"
    mock_tool.func = MagicMock(__name__="dummy")
    mock_tool.requires_approval = False

    with patch("flux.tasks.ai.agent_loop.progress", mock_progress):
        await run_agent_loop(
            llm_task=llm_task,
            formatter=mock_formatter,
            system_prompt="You are helpful.",
            instruction="Hello",
            tools=[mock_tool],
            tool_schemas=[{"name": "dummy", "parameters": {}}],
            stream=True,
        )

    reasoning_events = [e for e in events if e.get("type") == "reasoning"]
    assert len(reasoning_events) == 0


@pytest.mark.asyncio
async def test_reasoning_streamed_via_formatter_in_tool_loop(mock_formatter, progress_events):
    """When formatter supports reasoning stream, reasoning in tool loop is also streamed."""
    events, mock_progress = progress_events

    mock_formatter.supports_reasoning_stream = True

    call_count = 0

    async def fake_call_with_reasoning_stream(messages, call_kwargs, on_reasoning_token):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            for token in ["Thinking ", "about ", "tools."]:
                await on_reasoning_token(token)
            return LLMResponse(
                text="",
                tool_calls=[ToolCall(id="tc_1", name="search", arguments={"q": "test"})],
                reasoning=ReasoningContent(text="Thinking about tools."),
            )
        for token in ["Final ", "thoughts."]:
            await on_reasoning_token(token)
        return LLMResponse(
            text="Done.",
            reasoning=ReasoningContent(text="Final thoughts."),
        )

    mock_formatter.call_with_reasoning_stream = fake_call_with_reasoning_stream

    llm_task = MagicMock()
    llm_task.with_options.return_value = MagicMock()

    mock_tool = AsyncMock(return_value="ok")
    mock_tool.name = "search"
    mock_tool.func = MagicMock(__name__="search")
    mock_tool.requires_approval = False

    with patch("flux.tasks.ai.agent_loop.progress", mock_progress):
        with patch("flux.tasks.ai.agent_loop.execute_tools", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = [{"tool_call_id": "tc_1", "output": "result"}]

            await run_agent_loop(
                llm_task=llm_task,
                formatter=mock_formatter,
                system_prompt="Help.",
                instruction="Search.",
                tools=[mock_tool],
                tool_schemas=[{"name": "search", "parameters": {}}],
                stream=True,
            )

    reasoning_events = [e for e in events if e.get("type") == "reasoning"]
    assert len(reasoning_events) == 5
    assert [e["text"] for e in reasoning_events] == [
        "Thinking ",
        "about ",
        "tools.",
        "Final ",
        "thoughts.",
    ]
