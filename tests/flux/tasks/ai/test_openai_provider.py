import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from flux.domain.execution_context import ExecutionContext
from flux.tasks.ai.formatter import LLMFormatter
from flux.tasks.ai.models import LLMResponse, ToolCall


def test_build_returns_task_and_formatter():
    with patch("flux.tasks.ai.openai.AsyncOpenAI"):
        from flux.tasks.ai.openai import build_openai_provider

        llm_task, formatter = build_openai_provider("gpt-4o")

        assert callable(llm_task)
        assert isinstance(formatter, LLMFormatter)


def test_build_raises_without_openai():
    with patch("flux.tasks.ai.openai.AsyncOpenAI", None):
        from flux.tasks.ai.openai import build_openai_provider

        try:
            build_openai_provider("gpt-4o")
            assert False, "Expected ImportError"
        except ImportError as e:
            assert "openai" in str(e)


def test_build_messages_basic():
    with patch("flux.tasks.ai.openai.AsyncOpenAI"):
        from flux.tasks.ai.openai import build_openai_provider

        _, formatter = build_openai_provider("gpt-4o")

        messages, kwargs = formatter.build_messages("You are helpful.", "Hello")

        assert messages == [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        assert kwargs["model"] == "gpt-4o"


def test_build_messages_with_working_memory():
    with patch("flux.tasks.ai.openai.AsyncOpenAI"):
        from flux.tasks.ai.openai import build_openai_provider

        _, formatter = build_openai_provider("gpt-4o")

        memory = MagicMock()
        memory.recall.return_value = [
            {"role": "user", "content": "prior question"},
            {"role": "assistant", "content": "prior answer"},
        ]

        messages, kwargs = formatter.build_messages("sys", "new question", memory)

        assert len(messages) == 4
        assert messages[0] == {"role": "system", "content": "sys"}
        assert messages[1] == {"role": "user", "content": "prior question"}
        assert messages[2] == {"role": "assistant", "content": "prior answer"}
        assert messages[3] == {"role": "user", "content": "new question"}
        assert kwargs["model"] == "gpt-4o"


def test_format_assistant_message_text_only():
    with patch("flux.tasks.ai.openai.AsyncOpenAI"):
        from flux.tasks.ai.openai import build_openai_provider

        _, formatter = build_openai_provider("gpt-4o")

        response = LLMResponse(text="Hello world")
        msg = formatter.format_assistant_message(response)

        assert msg["role"] == "assistant"
        assert msg["content"] == "Hello world"
        assert "tool_calls" not in msg


def test_format_assistant_message_with_tool_calls():
    with patch("flux.tasks.ai.openai.AsyncOpenAI"):
        from flux.tasks.ai.openai import build_openai_provider

        _, formatter = build_openai_provider("gpt-4o")

        response = LLMResponse(
            text="Let me search.",
            tool_calls=[ToolCall(id="tc_1", name="search", arguments={"q": "AI"})],
        )
        msg = formatter.format_assistant_message(response)

        assert msg["role"] == "assistant"
        assert msg["content"] == "Let me search."
        assert len(msg["tool_calls"]) == 1
        assert msg["tool_calls"][0] == {
            "id": "tc_1",
            "type": "function",
            "function": {
                "name": "search",
                "arguments": json.dumps({"q": "AI"}),
            },
        }


def test_format_assistant_message_empty_response():
    with patch("flux.tasks.ai.openai.AsyncOpenAI"):
        from flux.tasks.ai.openai import build_openai_provider

        _, formatter = build_openai_provider("gpt-4o")

        response = LLMResponse()
        msg = formatter.format_assistant_message(response)

        assert msg["role"] == "assistant"
        assert msg["content"] is None
        assert "tool_calls" not in msg


def test_format_tool_results():
    with patch("flux.tasks.ai.openai.AsyncOpenAI"):
        from flux.tasks.ai.openai import build_openai_provider

        _, formatter = build_openai_provider("gpt-4o")

        tool_calls = [
            ToolCall(id="tc_1", name="search", arguments={"q": "AI"}),
            ToolCall(id="tc_2", name="read", arguments={"path": "/tmp"}),
        ]
        results = [
            {"output": "found 3 results"},
            {"output": "file contents here"},
        ]

        msgs = formatter.format_tool_results(tool_calls, results)

        assert len(msgs) == 2
        assert msgs[0] == {
            "role": "tool",
            "tool_call_id": "tc_1",
            "content": "found 3 results",
        }
        assert msgs[1] == {
            "role": "tool",
            "tool_call_id": "tc_2",
            "content": "file contents here",
        }


def test_format_user_message():
    with patch("flux.tasks.ai.openai.AsyncOpenAI"):
        from flux.tasks.ai.openai import build_openai_provider

        _, formatter = build_openai_provider("gpt-4o")

        msg = formatter.format_user_message("Continue working.")

        assert msg == {"role": "user", "content": "Continue working."}


def test_remove_tools_from_kwargs():
    with patch("flux.tasks.ai.openai.AsyncOpenAI"):
        from flux.tasks.ai.openai import build_openai_provider

        _, formatter = build_openai_provider("gpt-4o")

        kwargs = {
            "model": "gpt-4o",
            "tools": [{"type": "function", "function": {"name": "search"}}],
        }

        cleaned = formatter.remove_tools_from_kwargs(kwargs)

        assert "tools" not in cleaned
        assert cleaned["model"] == "gpt-4o"


def test_to_llm_response_text_only():
    from flux.tasks.ai.openai import _to_llm_response

    message = MagicMock()
    message.content = "Hello world"
    message.tool_calls = None
    message.reasoning_content = None

    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message = message

    result = _to_llm_response(response)

    assert isinstance(result, LLMResponse)
    assert result.text == "Hello world"
    assert result.tool_calls == []


def test_to_llm_response_with_tool_calls():
    from flux.tasks.ai.openai import _to_llm_response

    tc = MagicMock()
    tc.id = "call_123"
    tc.function.name = "search"
    tc.function.arguments = '{"q": "AI"}'

    message = MagicMock()
    message.content = "Let me search."
    message.tool_calls = [tc]
    message.reasoning_content = None

    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message = message

    result = _to_llm_response(response)

    assert result.text == "Let me search."
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "call_123"
    assert result.tool_calls[0].name == "search"
    assert result.tool_calls[0].arguments == {"q": "AI"}


def test_to_llm_response_empty():
    from flux.tasks.ai.openai import _to_llm_response

    message = MagicMock()
    message.content = None
    message.tool_calls = None
    message.reasoning_content = None

    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message = message

    result = _to_llm_response(response)

    assert result.text == ""
    assert result.tool_calls == []


def test_to_openai_tools():
    from flux.tasks.ai.openai import _to_openai_tools

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

    result = _to_openai_tools(schemas)

    assert len(result) == 1
    assert result[0]["type"] == "function"
    assert result[0]["function"]["name"] == "search"
    assert result[0]["function"]["description"] == "Search for something"
    assert result[0]["function"]["parameters"] == schemas[0]["parameters"]


def test_llm_task_returns_llm_response():
    async def run():
        message = MagicMock()
        message.content = "Hello!"
        message.tool_calls = None
        message.reasoning_content = None

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message = message

        with patch("flux.tasks.ai.openai.AsyncOpenAI") as MockOpenAI:
            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            MockOpenAI.return_value = mock_client

            from flux.tasks.ai.openai import build_openai_provider

            llm_task, _ = build_openai_provider("gpt-4o")

            ctx = ExecutionContext(workflow_id="wf1", workflow_namespace="default", workflow_name="test")
            token = ExecutionContext.set(ctx)
            try:
                result = await llm_task(
                    [{"role": "user", "content": "Hi"}],
                    model="gpt-4o",
                )
            finally:
                ExecutionContext.reset(token)

            assert isinstance(result, LLMResponse)
            assert result.text == "Hello!"
            assert result.tool_calls == []

            mock_client.chat.completions.create.assert_called_once_with(
                messages=[{"role": "user", "content": "Hi"}],
                model="gpt-4o",
            )

    asyncio.run(run())


def test_stream_yields_tokens():
    async def run():
        with patch("flux.tasks.ai.openai.AsyncOpenAI") as MockOpenAI:
            mock_client = MagicMock()
            MockOpenAI.return_value = mock_client

            async def mock_stream():
                for t in ["Hello", " world", "!"]:
                    chunk = MagicMock()
                    chunk.choices = [MagicMock()]
                    chunk.choices[0].delta.content = t
                    yield chunk

            mock_client.chat.completions.create = AsyncMock(return_value=mock_stream())

            from flux.tasks.ai.openai import build_openai_provider

            _, formatter = build_openai_provider("gpt-4o")

            messages = [{"role": "user", "content": "Hi"}]
            kwargs = {"model": "gpt-4o"}

            tokens = []
            async for tok in formatter.stream(messages, kwargs):
                tokens.append(tok)

            assert tokens == ["Hello", " world", "!"]

    asyncio.run(run())


def test_build_messages_with_response_format():
    with patch("flux.tasks.ai.openai.AsyncOpenAI"):
        from pydantic import BaseModel

        from flux.tasks.ai.openai import build_openai_provider

        class MyFormat(BaseModel):
            name: str
            value: int

        _, formatter = build_openai_provider("gpt-4o", response_format=MyFormat)

        messages, kwargs = formatter.build_messages("You are helpful.", "Hello")

        assert kwargs["model"] == "gpt-4o"
        assert kwargs["response_format"]["type"] == "json_schema"
        assert kwargs["response_format"]["json_schema"]["name"] == "MyFormat"
