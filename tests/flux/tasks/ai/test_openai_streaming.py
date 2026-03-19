import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from flux.domain.execution_context import ExecutionContext


def test_openai_streaming_emits_progress():
    captured_progress = []

    def on_progress(execution_id, task_id, task_name, value):
        captured_progress.append(value)

    async def run():
        from flux.tasks.ai.openai import build_openai_agent

        async def mock_stream():
            for token in ["Hello", " world", "!"]:
                chunk = MagicMock()
                chunk.choices = [MagicMock()]
                chunk.choices[0].delta.content = token
                yield chunk

        with patch("flux.tasks.ai.openai.AsyncOpenAI") as MockOpenAI:
            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_stream())
            MockOpenAI.return_value = mock_client

            agent_task = build_openai_agent(
                system_prompt="Test",
                model_name="gpt-4o",
                stream=True,
            )

            ctx = ExecutionContext(workflow_id="wf1", workflow_name="test")
            ctx.set_progress_callback(on_progress)
            token = ExecutionContext.set(ctx)
            try:
                result = await agent_task("Say hello")
            finally:
                ExecutionContext.reset(token)

            assert result == "Hello world!"
            assert len(captured_progress) == 3
            assert captured_progress[0] == {"token": "Hello"}

    asyncio.run(run())


def test_openai_no_streaming_when_disabled():
    captured_progress = []

    def on_progress(execution_id, task_id, task_name, value):
        captured_progress.append(value)

    async def run():
        from flux.tasks.ai.openai import build_openai_agent

        mock_message = MagicMock()
        mock_message.content = "Hello world!"
        mock_message.tool_calls = None
        mock_message.model_dump.return_value = {"role": "assistant", "content": "Hello world!"}
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message = mock_message

        with patch("flux.tasks.ai.openai.AsyncOpenAI") as MockOpenAI:
            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            MockOpenAI.return_value = mock_client

            agent_task = build_openai_agent(
                system_prompt="Test",
                model_name="gpt-4o",
                stream=False,
            )

            ctx = ExecutionContext(workflow_id="wf1", workflow_name="test")
            ctx.set_progress_callback(on_progress)
            token = ExecutionContext.set(ctx)
            try:
                result = await agent_task("Say hello")
            finally:
                ExecutionContext.reset(token)

            assert result == "Hello world!"
            assert len(captured_progress) == 0

    asyncio.run(run())
