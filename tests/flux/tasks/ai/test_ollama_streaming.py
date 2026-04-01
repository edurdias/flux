import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from flux.domain.execution_context import ExecutionContext
from flux._task_context import _CURRENT_TASK


def test_ollama_streaming_yields_tokens():
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

            messages = [
                {"role": "system", "content": "Test"},
                {"role": "user", "content": "Say hello"},
            ]
            kwargs = {"model": "llama3"}

            tokens = []
            async for tok in formatter.stream(messages, kwargs):
                tokens.append(tok)

            assert tokens == ["Hello", " world", "!"]

    asyncio.run(run())


def test_ollama_no_streaming_returns_response():
    async def run():
        with patch("flux.tasks.ai.ollama.AsyncClient") as MockClient:
            mock_client = MagicMock()
            mock_client.chat = AsyncMock(
                return_value={"message": {"content": "Hello world!"}},
            )
            MockClient.return_value = mock_client

            from flux.tasks.ai.ollama import build_ollama_provider

            llm_task, _ = build_ollama_provider("llama3")

            ctx = ExecutionContext(workflow_id="wf1", workflow_name="test")
            ctx_token = ExecutionContext.set(ctx)
            task_token = _CURRENT_TASK.set(("task-1", "test_task"))
            try:
                result = await llm_task(
                    [{"role": "user", "content": "Say hello"}],
                    model="llama3",
                )
            finally:
                _CURRENT_TASK.reset(task_token)
                ExecutionContext.reset(ctx_token)

            assert result.text == "Hello world!"
            assert result.tool_calls == []

    asyncio.run(run())


def test_ollama_llm_task_with_tool_calls():
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
                                    "name": "get_weather",
                                    "arguments": {"city": "London"},
                                },
                            },
                        ],
                    },
                },
            )
            MockClient.return_value = mock_client

            from flux.tasks.ai.ollama import build_ollama_provider

            llm_task, _ = build_ollama_provider("llama3")

            ctx = ExecutionContext(workflow_id="wf1", workflow_name="test")
            ctx_token = ExecutionContext.set(ctx)
            task_token = _CURRENT_TASK.set(("task-1", "test_task"))
            try:
                result = await llm_task(
                    [{"role": "user", "content": "Weather in London?"}],
                    model="llama3",
                )
            finally:
                _CURRENT_TASK.reset(task_token)
                ExecutionContext.reset(ctx_token)

            assert len(result.tool_calls) == 1
            assert result.tool_calls[0].name == "get_weather"
            assert result.tool_calls[0].arguments == {"city": "London"}

    asyncio.run(run())
