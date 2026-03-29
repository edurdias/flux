import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from flux.domain.execution_context import ExecutionContext
from flux._task_context import _CURRENT_TASK


def test_ollama_streaming_emits_progress():
    captured_progress = []

    def on_progress(execution_id, task_id, task_name, value):
        captured_progress.append(value)

    async def run():
        async def mock_chat(**kwargs):
            if kwargs.get("stream"):

                async def token_generator():
                    for token in ["Hello", " world", "!"]:
                        yield {"message": {"content": token}}

                return token_generator()
            else:
                return {"message": {"content": "Hello world!", "tool_calls": None}}

        with patch("ollama.AsyncClient") as MockClient:
            mock_client = MagicMock()
            mock_client.chat = mock_chat
            MockClient.return_value = mock_client

            from flux.tasks.ai.ollama import build_ollama_agent

            agent_task = build_ollama_agent(
                system_prompt="Test",
                model_name="llama3",
                stream=True,
            )

            ctx = ExecutionContext(workflow_id="wf1", workflow_name="test")
            ctx.set_progress_callback(on_progress)
            ctx_token = ExecutionContext.set(ctx)
            task_token = _CURRENT_TASK.set(("task-1", "test_task"))
            try:
                result = await agent_task("Say hello")
            finally:
                _CURRENT_TASK.reset(task_token)
                ExecutionContext.reset(ctx_token)

            assert result == "Hello world!"
            assert len(captured_progress) == 3
            assert captured_progress[0] == {"token": "Hello"}
            assert captured_progress[1] == {"token": " world"}
            assert captured_progress[2] == {"token": "!"}

    asyncio.run(run())


def test_ollama_no_streaming_when_disabled():
    captured_progress = []

    def on_progress(execution_id, task_id, task_name, value):
        captured_progress.append(value)

    async def run():
        with patch("ollama.AsyncClient") as MockClient:
            mock_client = MagicMock()
            mock_client.chat = AsyncMock(
                return_value={"message": {"content": "Hello world!", "tool_calls": None}},
            )
            MockClient.return_value = mock_client

            from flux.tasks.ai.ollama import build_ollama_agent

            agent_task = build_ollama_agent(
                system_prompt="Test",
                model_name="llama3",
                stream=False,
            )

            ctx = ExecutionContext(workflow_id="wf1", workflow_name="test")
            ctx.set_progress_callback(on_progress)
            ctx_token = ExecutionContext.set(ctx)
            task_token = _CURRENT_TASK.set(("task-1", "test_task"))
            try:
                result = await agent_task("Say hello")
            finally:
                _CURRENT_TASK.reset(task_token)
                ExecutionContext.reset(ctx_token)

            assert result == "Hello world!"
            assert len(captured_progress) == 0

    asyncio.run(run())


def test_ollama_streaming_with_tools_streams_final_response():
    captured_progress = []

    def on_progress(execution_id, task_id, task_name, value):
        captured_progress.append(value)

    async def run():
        call_count = 0

        async def mock_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            if kwargs.get("stream"):

                async def token_generator():
                    for token in ["Final", " answer"]:
                        yield {"message": {"content": token}}

                return token_generator()
            elif call_count == 1:
                return {
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
                }
            else:
                return {"message": {"content": "Sunny", "tool_calls": None}}

        async def get_weather(city: str) -> str:
            """Get the weather for a city."""
            return "Sunny"

        with patch("ollama.AsyncClient") as MockClient:
            mock_client = MagicMock()
            mock_client.chat = mock_chat
            MockClient.return_value = mock_client

            from flux.tasks.ai.ollama import build_ollama_agent

            agent_task = build_ollama_agent(
                system_prompt="Test",
                model_name="llama3",
                tools=[get_weather],
                stream=True,
            )

            ctx = ExecutionContext(workflow_id="wf1", workflow_name="test")
            ctx.set_progress_callback(on_progress)
            ctx_token = ExecutionContext.set(ctx)
            task_token = _CURRENT_TASK.set(("task-1", "test_task"))
            try:
                result = await agent_task("What is the weather in London?")
            finally:
                _CURRENT_TASK.reset(task_token)
                ExecutionContext.reset(ctx_token)

            assert result == "Sunny"
            assert len(captured_progress) == 0

    asyncio.run(run())
