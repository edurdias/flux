import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from flux.domain.execution_context import ExecutionContext
from flux.tasks.ai.formatter import LLMFormatter
from flux.tasks.ai.models import LLMResponse, ToolCall


def _mock_types():
    mock_types = MagicMock()

    class FakeContent:
        def __init__(self, *, role, parts):
            self.role = role
            self.parts = parts

    class FakePart:
        def __init__(self, *, text=None, function_call=None, function_response=None):
            self.text = text
            self.function_call = function_call
            self.function_response = function_response

    class FakeFunctionResponse:
        def __init__(self, *, name, response):
            self.name = name
            self.response = response

    class FakeFunctionDeclaration:
        def __init__(self, *, name, description, parameters_json_schema):
            self.name = name
            self.description = description
            self.parameters_json_schema = parameters_json_schema

    class FakeTool:
        def __init__(self, *, function_declarations):
            self.function_declarations = function_declarations

    class FakeGenerateContentConfig:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    mock_types.Content = FakeContent
    mock_types.Part = FakePart
    mock_types.FunctionResponse = FakeFunctionResponse
    mock_types.FunctionDeclaration = FakeFunctionDeclaration
    mock_types.Tool = FakeTool
    mock_types.GenerateContentConfig = FakeGenerateContentConfig
    return mock_types


def test_build_returns_task_and_formatter():
    mock_types = _mock_types()
    with (
        patch("flux.tasks.ai.gemini.genai", MagicMock()),
        patch("flux.tasks.ai.gemini.types", mock_types),
    ):
        from flux.tasks.ai.gemini import build_gemini_provider

        llm_task, formatter = build_gemini_provider("gemini-2.5-flash")

        assert callable(llm_task)
        assert isinstance(formatter, LLMFormatter)


def test_build_raises_without_genai():
    with patch("flux.tasks.ai.gemini.genai", None):
        from flux.tasks.ai.gemini import build_gemini_provider

        try:
            build_gemini_provider("gemini-2.5-flash")
            assert False, "Expected ImportError"
        except ImportError as e:
            assert "google-genai" in str(e)


def test_build_messages_basic():
    mock_types = _mock_types()
    with (
        patch("flux.tasks.ai.gemini.genai", MagicMock()),
        patch("flux.tasks.ai.gemini.types", mock_types),
    ):
        from flux.tasks.ai.gemini import build_gemini_provider

        _, formatter = build_gemini_provider("gemini-2.5-flash", max_tokens=2048)

        messages, kwargs = formatter.build_messages("You are helpful.", "Hello")

        assert len(messages) == 1
        assert messages[0].role == "user"
        assert messages[0].parts[0].text == "Hello"
        assert kwargs["model"] == "gemini-2.5-flash"
        config = kwargs["config"]
        assert config.system_instruction == "You are helpful."
        assert config.max_output_tokens == 2048


def test_build_messages_with_working_memory():
    mock_types = _mock_types()
    with (
        patch("flux.tasks.ai.gemini.genai", MagicMock()),
        patch("flux.tasks.ai.gemini.types", mock_types),
    ):
        from flux.tasks.ai.gemini import build_gemini_provider

        _, formatter = build_gemini_provider("gemini-2.5-flash")

        memory = MagicMock()
        memory.recall.return_value = [
            {"role": "user", "content": "prior question"},
            {"role": "assistant", "content": "prior answer"},
        ]

        messages, kwargs = formatter.build_messages("sys", "new question", memory)

        assert len(messages) == 3
        assert messages[0].role == "user"
        assert messages[0].parts[0].text == "prior question"
        assert messages[1].role == "model"
        assert messages[1].parts[0].text == "prior answer"
        assert messages[2].role == "user"
        assert messages[2].parts[0].text == "new question"


def test_format_assistant_message_text_only():
    mock_types = _mock_types()
    with (
        patch("flux.tasks.ai.gemini.genai", MagicMock()),
        patch("flux.tasks.ai.gemini.types", mock_types),
    ):
        from flux.tasks.ai.gemini import build_gemini_provider

        _, formatter = build_gemini_provider("gemini-2.5-flash")

        response = LLMResponse(text="Hello world")
        msg = formatter.format_assistant_message(response)

        assert msg.role == "model"
        assert len(msg.parts) == 1
        assert msg.parts[0].text == "Hello world"


def test_format_assistant_message_with_tool_calls():
    mock_types = _mock_types()
    with (
        patch("flux.tasks.ai.gemini.genai", MagicMock()),
        patch("flux.tasks.ai.gemini.types", mock_types),
    ):
        from flux.tasks.ai.gemini import build_gemini_provider

        _, formatter = build_gemini_provider("gemini-2.5-flash")

        response = LLMResponse(
            text="Let me search.",
            tool_calls=[ToolCall(id="search", name="search", arguments={"q": "AI"})],
        )
        msg = formatter.format_assistant_message(response)

        assert msg.role == "model"
        assert len(msg.parts) == 2
        assert msg.parts[0].text == "Let me search."
        assert msg.parts[1].function_call is not None


def test_format_assistant_message_empty_response():
    mock_types = _mock_types()
    with (
        patch("flux.tasks.ai.gemini.genai", MagicMock()),
        patch("flux.tasks.ai.gemini.types", mock_types),
    ):
        from flux.tasks.ai.gemini import build_gemini_provider

        _, formatter = build_gemini_provider("gemini-2.5-flash")

        response = LLMResponse()
        msg = formatter.format_assistant_message(response)

        assert msg.role == "model"
        assert len(msg.parts) == 0


def test_format_tool_results():
    mock_types = _mock_types()
    with (
        patch("flux.tasks.ai.gemini.genai", MagicMock()),
        patch("flux.tasks.ai.gemini.types", mock_types),
    ):
        from flux.tasks.ai.gemini import build_gemini_provider

        _, formatter = build_gemini_provider("gemini-2.5-flash")

        tool_calls = [
            ToolCall(id="search", name="search", arguments={"q": "AI"}),
            ToolCall(id="read", name="read", arguments={"path": "/tmp"}),
        ]
        results = [
            {"output": "found 3 results"},
            {"output": "file contents here"},
        ]

        msgs = formatter.format_tool_results(tool_calls, results)

        assert len(msgs) == 1
        content = msgs[0]
        assert content.role == "user"
        assert len(content.parts) == 2
        assert content.parts[0].function_response.name == "search"
        assert content.parts[0].function_response.response == {"output": "found 3 results"}
        assert content.parts[1].function_response.name == "read"
        assert content.parts[1].function_response.response == {"output": "file contents here"}


def test_format_user_message():
    mock_types = _mock_types()
    with (
        patch("flux.tasks.ai.gemini.genai", MagicMock()),
        patch("flux.tasks.ai.gemini.types", mock_types),
    ):
        from flux.tasks.ai.gemini import build_gemini_provider

        _, formatter = build_gemini_provider("gemini-2.5-flash")

        msg = formatter.format_user_message("Continue working.")

        assert msg.role == "user"
        assert msg.parts[0].text == "Continue working."


def test_remove_tools_from_kwargs():
    mock_types = _mock_types()
    with (
        patch("flux.tasks.ai.gemini.genai", MagicMock()),
        patch("flux.tasks.ai.gemini.types", mock_types),
    ):
        from flux.tasks.ai.gemini import build_gemini_provider

        _, formatter = build_gemini_provider("gemini-2.5-flash")

        config_with_tools = mock_types.GenerateContentConfig(
            system_instruction="You are helpful.",
            max_output_tokens=4096,
            tools=[mock_types.Tool(function_declarations=[])],
        )

        kwargs = {
            "model": "gemini-2.5-flash",
            "config": config_with_tools,
        }

        cleaned = formatter.remove_tools_from_kwargs(kwargs)

        assert cleaned["model"] == "gemini-2.5-flash"
        assert cleaned["config"].system_instruction == "You are helpful."
        assert cleaned["config"].max_output_tokens == 4096
        assert not hasattr(cleaned["config"], "tools") or cleaned["config"].tools is None


def test_to_llm_response_text_only():
    from flux.tasks.ai.gemini import _to_llm_response

    response = MagicMock()
    response.text = "Hello world"
    response.function_calls = None

    result = _to_llm_response(response)

    assert isinstance(result, LLMResponse)
    assert result.text == "Hello world"
    assert result.tool_calls == []


def test_to_llm_response_with_function_calls():
    from flux.tasks.ai.gemini import _to_llm_response

    fc = MagicMock()
    fc.name = "search"
    fc.args = {"q": "AI"}

    response = MagicMock()
    response.text = "Let me search."
    response.function_calls = [fc]

    result = _to_llm_response(response)

    assert result.text == "Let me search."
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "search"
    assert result.tool_calls[0].name == "search"
    assert result.tool_calls[0].arguments == {"q": "AI"}


def test_to_llm_response_empty():
    from flux.tasks.ai.gemini import _to_llm_response

    response = MagicMock()
    response.text = None
    response.function_calls = None

    result = _to_llm_response(response)

    assert result.text == ""
    assert result.tool_calls == []


def test_to_gemini_tools():
    mock_types = _mock_types()
    with patch("flux.tasks.ai.gemini.types", mock_types):
        from flux.tasks.ai.gemini import _to_gemini_tools

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

        result = _to_gemini_tools(schemas)

        assert len(result) == 1
        tool = result[0]
        assert len(tool.function_declarations) == 1
        decl = tool.function_declarations[0]
        assert decl.name == "search"
        assert decl.description == "Search for something"
        assert decl.parameters_json_schema == schemas[0]["parameters"]


def test_llm_task_returns_llm_response():
    mock_types = _mock_types()

    async def run():
        mock_response = MagicMock()
        mock_response.text = "Hello!"
        mock_response.function_calls = None

        with (
            patch("flux.tasks.ai.gemini.genai") as mock_genai,
            patch("flux.tasks.ai.gemini.types", mock_types),
        ):
            mock_client = MagicMock()
            mock_client.aio.models.generate_content = AsyncMock(
                return_value=mock_response,
            )
            mock_genai.Client.return_value = mock_client

            from flux.tasks.ai.gemini import build_gemini_provider

            llm_task, _ = build_gemini_provider("gemini-2.5-flash")

            ctx = ExecutionContext(
                workflow_id="wf1", workflow_namespace="default", workflow_name="test",
            )
            token = ExecutionContext.set(ctx)
            try:
                config = mock_types.GenerateContentConfig(
                    system_instruction="You are helpful.",
                    max_output_tokens=4096,
                )
                contents = [mock_types.Content(role="user", parts=[mock_types.Part(text="Hi")])]
                result = await llm_task(
                    contents,
                    config=config,
                    model="gemini-2.5-flash",
                )
            finally:
                ExecutionContext.reset(token)

            assert isinstance(result, LLMResponse)
            assert result.text == "Hello!"
            assert result.tool_calls == []

            mock_client.aio.models.generate_content.assert_called_once_with(
                model="gemini-2.5-flash",
                contents=contents,
                config=config,
            )

    asyncio.run(run())


def test_stream_yields_tokens():
    mock_types = _mock_types()

    async def run():
        with (
            patch("flux.tasks.ai.gemini.genai") as mock_genai,
            patch("flux.tasks.ai.gemini.types", mock_types),
        ):
            mock_client = MagicMock()

            async def mock_stream():
                for t in ["Hello", " world", "!"]:
                    chunk = MagicMock()
                    chunk.text = t
                    yield chunk

            mock_client.aio.models.generate_content_stream = AsyncMock(
                return_value=mock_stream(),
            )
            mock_genai.Client.return_value = mock_client

            from flux.tasks.ai.gemini import build_gemini_provider

            _, formatter = build_gemini_provider("gemini-2.5-flash")

            contents = [mock_types.Content(role="user", parts=[mock_types.Part(text="Hi")])]
            config = mock_types.GenerateContentConfig(
                system_instruction="sys",
                max_output_tokens=4096,
            )
            kwargs = {"model": "gemini-2.5-flash", "config": config}

            tokens = []
            async for tok in formatter.stream(contents, kwargs):
                tokens.append(tok)

            assert tokens == ["Hello", " world", "!"]

    asyncio.run(run())


def test_build_messages_with_response_format():
    mock_types = _mock_types()
    with (
        patch("flux.tasks.ai.gemini.genai", MagicMock()),
        patch("flux.tasks.ai.gemini.types", mock_types),
    ):
        from pydantic import BaseModel

        from flux.tasks.ai.gemini import build_gemini_provider

        class MyFormat(BaseModel):
            name: str
            value: int

        _, formatter = build_gemini_provider(
            "gemini-2.5-flash",
            response_format=MyFormat,
        )

        messages, kwargs = formatter.build_messages("You are helpful.", "Hello")

        config = kwargs["config"]
        assert config.response_mime_type == "application/json"
        assert config.response_schema is MyFormat
