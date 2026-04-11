import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from flux.domain.execution_context import ExecutionContext
from flux.tasks.ai.formatter import LLMFormatter
from flux.tasks.ai.models import LLMResponse, ToolCall


def test_build_returns_task_and_formatter():
    with patch("flux.tasks.ai.ollama.AsyncClient"):
        from flux.tasks.ai.ollama import build_ollama_provider

        llm_task, formatter = build_ollama_provider("llama3")

        assert callable(llm_task)
        assert isinstance(formatter, LLMFormatter)


def test_build_raises_without_ollama():
    with patch("flux.tasks.ai.ollama.AsyncClient", None):
        from flux.tasks.ai.ollama import build_ollama_provider

        try:
            build_ollama_provider("llama3")
            assert False, "Expected ImportError"
        except ImportError as e:
            assert "ollama" in str(e)


def test_build_messages_basic():
    with patch("flux.tasks.ai.ollama.AsyncClient"):
        from flux.tasks.ai.ollama import build_ollama_provider

        _, formatter = build_ollama_provider("llama3")

        messages, kwargs = formatter.build_messages("You are helpful.", "Hello")

        assert messages == [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        assert kwargs["model"] == "llama3"


def test_build_messages_with_working_memory():
    with patch("flux.tasks.ai.ollama.AsyncClient"):
        from flux.tasks.ai.ollama import build_ollama_provider

        _, formatter = build_ollama_provider("llama3")

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
        assert kwargs["model"] == "llama3"


def test_build_messages_with_response_format():
    with patch("flux.tasks.ai.ollama.AsyncClient"):
        from pydantic import BaseModel

        from flux.tasks.ai.ollama import build_ollama_provider

        class MyFormat(BaseModel):
            name: str
            value: int

        _, formatter = build_ollama_provider("llama3", response_format=MyFormat)

        messages, kwargs = formatter.build_messages("You are helpful.", "Hello")

        assert kwargs["model"] == "llama3"
        assert kwargs["format"] == "json"
        assert "Respond with JSON matching this schema:" in messages[-1]["content"]


def test_build_messages_includes_tool_names():
    with patch("flux.tasks.ai.ollama.AsyncClient"):
        from flux.tasks.ai.ollama import build_ollama_provider

        _, formatter = build_ollama_provider("llama3")
        formatter.set_tool_names({"search", "read_file"})

        messages, kwargs = formatter.build_messages("sys", "Hello")

        assert kwargs["tool_names"] == {"search", "read_file"}


def test_format_assistant_message_text_only():
    with patch("flux.tasks.ai.ollama.AsyncClient"):
        from flux.tasks.ai.ollama import build_ollama_provider

        _, formatter = build_ollama_provider("llama3")

        response = LLMResponse(text="Hello world")
        msg = formatter.format_assistant_message(response)

        assert msg["role"] == "assistant"
        assert msg["content"] == "Hello world"
        assert "tool_calls" not in msg


def test_format_assistant_message_with_tool_calls():
    with patch("flux.tasks.ai.ollama.AsyncClient"):
        from flux.tasks.ai.ollama import build_ollama_provider

        _, formatter = build_ollama_provider("llama3")

        response = LLMResponse(
            text="Let me search.",
            tool_calls=[ToolCall(id="call_0", name="search", arguments={"q": "AI"})],
        )
        msg = formatter.format_assistant_message(response)

        assert msg["role"] == "assistant"
        assert msg["content"] == "Let me search."
        assert len(msg["tool_calls"]) == 1
        assert msg["tool_calls"][0] == {
            "function": {
                "name": "search",
                "arguments": {"q": "AI"},
            },
        }


def test_format_assistant_message_empty_response():
    with patch("flux.tasks.ai.ollama.AsyncClient"):
        from flux.tasks.ai.ollama import build_ollama_provider

        _, formatter = build_ollama_provider("llama3")

        response = LLMResponse()
        msg = formatter.format_assistant_message(response)

        assert msg["role"] == "assistant"
        assert msg["content"] == ""
        assert "tool_calls" not in msg


def test_format_tool_results():
    with patch("flux.tasks.ai.ollama.AsyncClient"):
        from flux.tasks.ai.ollama import build_ollama_provider

        _, formatter = build_ollama_provider("llama3")

        tool_calls = [
            ToolCall(id="call_0", name="search", arguments={"q": "AI"}),
            ToolCall(id="call_1", name="read", arguments={"path": "/tmp"}),
        ]
        results = [
            {"output": "found 3 results"},
            {"output": "file contents here"},
        ]

        msgs = formatter.format_tool_results(tool_calls, results)

        assert len(msgs) == 2
        assert msgs[0] == {"role": "tool", "content": "found 3 results"}
        assert msgs[1] == {"role": "tool", "content": "file contents here"}


def test_format_user_message():
    with patch("flux.tasks.ai.ollama.AsyncClient"):
        from flux.tasks.ai.ollama import build_ollama_provider

        _, formatter = build_ollama_provider("llama3")

        msg = formatter.format_user_message("Continue working.")

        assert msg == {"role": "user", "content": "Continue working."}


def test_remove_tools_from_kwargs():
    with patch("flux.tasks.ai.ollama.AsyncClient"):
        from flux.tasks.ai.ollama import build_ollama_provider

        _, formatter = build_ollama_provider("llama3")

        kwargs = {
            "model": "llama3",
            "tools": [{"type": "function", "function": {"name": "search"}}],
        }

        cleaned = formatter.remove_tools_from_kwargs(kwargs)

        assert "tools" not in cleaned
        assert cleaned["model"] == "llama3"


def test_to_llm_response_text_only():
    from flux.tasks.ai.ollama import _to_llm_response

    response = {"message": {"content": "Hello world"}}

    result = _to_llm_response(response)

    assert isinstance(result, LLMResponse)
    assert result.text == "Hello world"
    assert result.tool_calls == []


def test_to_llm_response_with_structured_tool_calls():
    from flux.tasks.ai.ollama import _to_llm_response

    response = {
        "message": {
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "search",
                        "arguments": {"q": "AI"},
                    },
                },
            ],
        },
    }

    result = _to_llm_response(response)

    assert result.text == ""
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "call_0"
    assert result.tool_calls[0].name == "search"
    assert result.tool_calls[0].arguments == {"q": "AI"}


def test_to_llm_response_with_text_extracted_tool_calls():
    from flux.tasks.ai.ollama import _to_llm_response

    content = '[TOOL_CALLS] [{"name": "search", "arguments": {"q": "AI"}}]'
    response = {"message": {"content": content}}

    result = _to_llm_response(response, tool_names={"search"})

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "search"
    assert result.tool_calls[0].arguments == {"q": "AI"}
    assert "[TOOL_CALLS]" not in result.text


def test_to_llm_response_text_extraction_no_match():
    from flux.tasks.ai.ollama import _to_llm_response

    response = {"message": {"content": "Just regular text"}}

    result = _to_llm_response(response, tool_names={"search"})

    assert result.text == "Just regular text"
    assert result.tool_calls == []


def test_to_llm_response_empty():
    from flux.tasks.ai.ollama import _to_llm_response

    response = {"message": {"content": ""}}

    result = _to_llm_response(response)

    assert result.text == ""
    assert result.tool_calls == []


def test_to_ollama_tools():
    from flux.tasks.ai.ollama import _to_ollama_tools

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

    result = _to_ollama_tools(schemas)

    assert len(result) == 1
    assert result[0]["type"] == "function"
    assert result[0]["function"]["name"] == "search"
    assert result[0]["function"]["description"] == "Search for something"
    assert result[0]["function"]["parameters"] == schemas[0]["parameters"]


def test_llm_task_returns_llm_response_structured_tool_calls():
    async def run():
        with patch("flux.tasks.ai.ollama.AsyncClient") as MockClient:
            mock_client = MagicMock()
            mock_client.chat = AsyncMock(
                return_value={
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "search",
                                    "arguments": {"q": "AI"},
                                },
                            },
                        ],
                    },
                },
            )
            MockClient.return_value = mock_client

            from flux.tasks.ai.ollama import build_ollama_provider

            llm_task, _ = build_ollama_provider("llama3")

            ctx = ExecutionContext(
                workflow_id="wf1", workflow_namespace="default", workflow_name="test",
            )
            token = ExecutionContext.set(ctx)
            try:
                result = await llm_task(
                    [{"role": "user", "content": "Hi"}],
                    model="llama3",
                )
            finally:
                ExecutionContext.reset(token)

            assert isinstance(result, LLMResponse)
            assert len(result.tool_calls) == 1
            assert result.tool_calls[0].name == "search"

    asyncio.run(run())


def test_llm_task_returns_llm_response_text_extracted_tool_calls():
    async def run():
        content = '[TOOL_CALLS] [{"name": "search", "arguments": {"q": "AI"}}]'
        with patch("flux.tasks.ai.ollama.AsyncClient") as MockClient:
            mock_client = MagicMock()
            mock_client.chat = AsyncMock(
                return_value={"message": {"content": content}},
            )
            MockClient.return_value = mock_client

            from flux.tasks.ai.ollama import build_ollama_provider

            llm_task, _ = build_ollama_provider("llama3")

            ctx = ExecutionContext(
                workflow_id="wf1", workflow_namespace="default", workflow_name="test",
            )
            token = ExecutionContext.set(ctx)
            try:
                result = await llm_task(
                    [{"role": "user", "content": "Hi"}],
                    model="llama3",
                    tool_names={"search"},
                )
            finally:
                ExecutionContext.reset(token)

            assert isinstance(result, LLMResponse)
            assert len(result.tool_calls) == 1
            assert result.tool_calls[0].name == "search"
            assert "[TOOL_CALLS]" not in result.text

    asyncio.run(run())


def test_llm_task_returns_llm_response_text_only():
    async def run():
        with patch("flux.tasks.ai.ollama.AsyncClient") as MockClient:
            mock_client = MagicMock()
            mock_client.chat = AsyncMock(
                return_value={"message": {"content": "Hello!"}},
            )
            MockClient.return_value = mock_client

            from flux.tasks.ai.ollama import build_ollama_provider

            llm_task, _ = build_ollama_provider("llama3")

            ctx = ExecutionContext(
                workflow_id="wf1", workflow_namespace="default", workflow_name="test",
            )
            token = ExecutionContext.set(ctx)
            try:
                result = await llm_task(
                    [{"role": "user", "content": "Hi"}],
                    model="llama3",
                )
            finally:
                ExecutionContext.reset(token)

            assert isinstance(result, LLMResponse)
            assert result.text == "Hello!"
            assert result.tool_calls == []

            mock_client.chat.assert_called_once_with(
                messages=[{"role": "user", "content": "Hi"}],
                model="llama3",
            )

    asyncio.run(run())


def test_stream_yields_tokens():
    async def run():
        with patch("flux.tasks.ai.ollama.AsyncClient") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client

            async def mock_chat(**kwargs):
                for t in ["Hello", " world", "!"]:
                    yield {"message": {"content": t}}

            mock_client.chat = AsyncMock(return_value=mock_chat())

            from flux.tasks.ai.ollama import build_ollama_provider

            _, formatter = build_ollama_provider("llama3")

            messages = [{"role": "user", "content": "Hi"}]
            kwargs = {"model": "llama3"}

            tokens = []
            async for tok in formatter.stream(messages, kwargs):
                tokens.append(tok)

            assert tokens == ["Hello", " world", "!"]

    asyncio.run(run())


def test_stream_strips_tool_names_from_kwargs():
    async def run():
        with patch("flux.tasks.ai.ollama.AsyncClient") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client

            captured_kwargs = {}

            async def mock_chat(**kwargs):
                captured_kwargs.update(kwargs)
                for t in ["done"]:
                    yield {"message": {"content": t}}

            mock_client.chat = AsyncMock(return_value=mock_chat())

            from flux.tasks.ai.ollama import build_ollama_provider

            _, formatter = build_ollama_provider("llama3")

            messages = [{"role": "user", "content": "Hi"}]
            kwargs = {"model": "llama3", "tool_names": {"search"}}

            tokens = []
            async for tok in formatter.stream(messages, kwargs):
                tokens.append(tok)

            assert "tool_names" not in captured_kwargs

    asyncio.run(run())
