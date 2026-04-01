import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from flux.domain.execution_context import ExecutionContext
from flux.tasks.ai.formatter import LLMFormatter
from flux.tasks.ai.models import LLMResponse, ToolCall


def test_build_returns_task_and_formatter():
    with patch("flux.tasks.ai.anthropic.AsyncAnthropic"):
        from flux.tasks.ai.anthropic import build_anthropic_provider

        llm_task, formatter = build_anthropic_provider("claude-sonnet-4-20250514")

        assert callable(llm_task)
        assert isinstance(formatter, LLMFormatter)


def test_build_raises_without_anthropic():
    with patch("flux.tasks.ai.anthropic.AsyncAnthropic", None):
        from flux.tasks.ai.anthropic import build_anthropic_provider

        try:
            build_anthropic_provider("claude-sonnet-4-20250514")
            assert False, "Expected ImportError"
        except ImportError as e:
            assert "anthropic" in str(e)


def test_build_messages_basic():
    with patch("flux.tasks.ai.anthropic.AsyncAnthropic"):
        from flux.tasks.ai.anthropic import build_anthropic_provider

        _, formatter = build_anthropic_provider("claude-sonnet-4-20250514", max_tokens=2048)

        messages, kwargs = formatter.build_messages("You are helpful.", "Hello")

        assert messages == [{"role": "user", "content": "Hello"}]
        assert kwargs["model"] == "claude-sonnet-4-20250514"
        assert kwargs["system"] == "You are helpful."
        assert kwargs["max_tokens"] == 2048


def test_build_messages_with_working_memory():
    with patch("flux.tasks.ai.anthropic.AsyncAnthropic"):
        from flux.tasks.ai.anthropic import build_anthropic_provider

        _, formatter = build_anthropic_provider("claude-sonnet-4-20250514")

        memory = MagicMock()
        memory.recall.return_value = [
            {"role": "user", "content": "prior question"},
            {"role": "assistant", "content": "prior answer"},
        ]

        messages, kwargs = formatter.build_messages("sys", "new question", memory)

        assert len(messages) == 3
        assert messages[0] == {"role": "user", "content": "prior question"}
        assert messages[1] == {"role": "assistant", "content": "prior answer"}
        assert messages[2] == {"role": "user", "content": "new question"}
        assert kwargs["system"] == "sys"


def test_format_assistant_message_text_only():
    with patch("flux.tasks.ai.anthropic.AsyncAnthropic"):
        from flux.tasks.ai.anthropic import build_anthropic_provider

        _, formatter = build_anthropic_provider("claude-sonnet-4-20250514")

        response = LLMResponse(text="Hello world")
        msg = formatter.format_assistant_message(response)

        assert msg["role"] == "assistant"
        assert msg["content"] == [{"type": "text", "text": "Hello world"}]


def test_format_assistant_message_with_tool_calls():
    with patch("flux.tasks.ai.anthropic.AsyncAnthropic"):
        from flux.tasks.ai.anthropic import build_anthropic_provider

        _, formatter = build_anthropic_provider("claude-sonnet-4-20250514")

        response = LLMResponse(
            text="Let me search.",
            tool_calls=[ToolCall(id="tc_1", name="search", arguments={"q": "AI"})],
        )
        msg = formatter.format_assistant_message(response)

        assert msg["role"] == "assistant"
        assert len(msg["content"]) == 2
        assert msg["content"][0] == {"type": "text", "text": "Let me search."}
        assert msg["content"][1] == {
            "type": "tool_use",
            "id": "tc_1",
            "name": "search",
            "input": {"q": "AI"},
        }


def test_format_tool_results():
    with patch("flux.tasks.ai.anthropic.AsyncAnthropic"):
        from flux.tasks.ai.anthropic import build_anthropic_provider

        _, formatter = build_anthropic_provider("claude-sonnet-4-20250514")

        tool_calls = [
            ToolCall(id="tc_1", name="search", arguments={"q": "AI"}),
            ToolCall(id="tc_2", name="read", arguments={"path": "/tmp"}),
        ]
        results = [
            {"output": "found 3 results"},
            {"output": "file contents here"},
        ]

        msgs = formatter.format_tool_results(tool_calls, results)

        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        content = msgs[0]["content"]
        assert len(content) == 2
        assert content[0] == {
            "type": "tool_result",
            "tool_use_id": "tc_1",
            "content": "found 3 results",
        }
        assert content[1] == {
            "type": "tool_result",
            "tool_use_id": "tc_2",
            "content": "file contents here",
        }


def test_format_user_message():
    with patch("flux.tasks.ai.anthropic.AsyncAnthropic"):
        from flux.tasks.ai.anthropic import build_anthropic_provider

        _, formatter = build_anthropic_provider("claude-sonnet-4-20250514")

        msg = formatter.format_user_message("Continue working.")

        assert msg == {"role": "user", "content": "Continue working."}


def test_remove_tools_from_kwargs():
    with patch("flux.tasks.ai.anthropic.AsyncAnthropic"):
        from flux.tasks.ai.anthropic import build_anthropic_provider

        _, formatter = build_anthropic_provider("claude-sonnet-4-20250514")

        kwargs = {
            "model": "claude-sonnet-4-20250514",
            "system": "You are helpful.",
            "max_tokens": 4096,
            "tools": [{"name": "search"}],
        }

        cleaned = formatter.remove_tools_from_kwargs(kwargs)

        assert "tools" not in cleaned
        assert cleaned["model"] == "claude-sonnet-4-20250514"
        assert cleaned["system"] == "You are helpful."
        assert cleaned["max_tokens"] == 4096


def test_to_llm_response_text_only():
    from flux.tasks.ai.anthropic import _to_llm_response

    response = MagicMock()
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "Hello world"
    response.content = [text_block]

    result = _to_llm_response(response)

    assert isinstance(result, LLMResponse)
    assert result.text == "Hello world"
    assert result.tool_calls == []


def test_to_llm_response_tool_use():
    from flux.tasks.ai.anthropic import _to_llm_response

    response = MagicMock()
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "Let me search."

    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.id = "tc_1"
    tool_block.name = "search"
    tool_block.input = {"q": "AI"}

    response.content = [text_block, tool_block]

    result = _to_llm_response(response)

    assert result.text == "Let me search."
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "tc_1"
    assert result.tool_calls[0].name == "search"
    assert result.tool_calls[0].arguments == {"q": "AI"}


def test_to_llm_response_empty():
    from flux.tasks.ai.anthropic import _to_llm_response

    response = MagicMock()
    response.content = []

    result = _to_llm_response(response)

    assert result.text == ""
    assert result.tool_calls == []


def test_to_anthropic_tools():
    from flux.tasks.ai.anthropic import _to_anthropic_tools

    schemas = [
        {
            "name": "search",
            "description": "Search for something",
            "parameters": {
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
        },
    ]

    result = _to_anthropic_tools(schemas)

    assert len(result) == 1
    assert result[0]["name"] == "search"
    assert result[0]["description"] == "Search for something"
    assert result[0]["input_schema"] == schemas[0]["parameters"]


def test_llm_task_returns_llm_response():
    async def run():
        mock_response = MagicMock()
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Hello!"
        mock_response.content = [text_block]

        with patch("flux.tasks.ai.anthropic.AsyncAnthropic") as MockAnthropic:
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            MockAnthropic.return_value = mock_client

            from flux.tasks.ai.anthropic import build_anthropic_provider

            llm_task, _ = build_anthropic_provider("claude-sonnet-4-20250514")

            ctx = ExecutionContext(workflow_id="wf1", workflow_name="test")
            token = ExecutionContext.set(ctx)
            try:
                result = await llm_task(
                    [{"role": "user", "content": "Hi"}],
                    model="claude-sonnet-4-20250514",
                    system="You are helpful.",
                    max_tokens=4096,
                )
            finally:
                ExecutionContext.reset(token)

            assert isinstance(result, LLMResponse)
            assert result.text == "Hello!"
            assert result.tool_calls == []

            mock_client.messages.create.assert_called_once_with(
                messages=[{"role": "user", "content": "Hi"}],
                model="claude-sonnet-4-20250514",
                system="You are helpful.",
                max_tokens=4096,
            )

    asyncio.run(run())


def test_stream_yields_tokens():
    async def run():
        with patch("flux.tasks.ai.anthropic.AsyncAnthropic") as MockAnthropic:
            mock_client = MagicMock()
            MockAnthropic.return_value = mock_client

            mock_stream_cm = MagicMock()

            async def mock_text_stream():
                for t in ["Hello", " world", "!"]:
                    yield t

            stream_obj = MagicMock()
            stream_obj.text_stream = mock_text_stream()
            mock_stream_cm.__aenter__ = AsyncMock(return_value=stream_obj)
            mock_stream_cm.__aexit__ = AsyncMock(return_value=False)
            mock_client.messages.stream = MagicMock(return_value=mock_stream_cm)

            from flux.tasks.ai.anthropic import build_anthropic_provider

            _, formatter = build_anthropic_provider("claude-sonnet-4-20250514")

            messages = [{"role": "user", "content": "Hi"}]
            kwargs = {"model": "claude-sonnet-4-20250514", "system": "sys", "max_tokens": 4096}

            tokens = []
            async for tok in formatter.stream(messages, kwargs):
                tokens.append(tok)

            assert tokens == ["Hello", " world", "!"]

    asyncio.run(run())


def test_format_assistant_message_empty_response():
    with patch("flux.tasks.ai.anthropic.AsyncAnthropic"):
        from flux.tasks.ai.anthropic import build_anthropic_provider

        _, formatter = build_anthropic_provider("claude-sonnet-4-20250514")

        response = LLMResponse()
        msg = formatter.format_assistant_message(response)

        assert msg["role"] == "assistant"
        assert msg["content"] == []
