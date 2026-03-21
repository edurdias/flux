import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from flux.domain.execution_context import ExecutionContext


def test_gemini_streaming_emits_progress():
    captured_progress = []

    def on_progress(execution_id, task_id, task_name, value):
        captured_progress.append(value)

    async def run():
        from flux.tasks.ai.gemini import build_gemini_agent

        async def mock_stream():
            for token in ["Hello", " world", "!"]:
                chunk = MagicMock()
                chunk.text = token
                yield chunk

        with patch("flux.tasks.ai.gemini.genai") as mock_genai:
            mock_client = MagicMock()
            mock_client.aio.models.generate_content_stream = AsyncMock(
                return_value=mock_stream(),
            )
            mock_genai.Client.return_value = mock_client

            agent_task = build_gemini_agent(
                system_prompt="Test",
                model_name="gemini-2.5-flash",
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
            assert captured_progress[1] == {"token": " world"}
            assert captured_progress[2] == {"token": "!"}

    asyncio.run(run())


def test_gemini_no_streaming_when_disabled():
    captured_progress = []

    def on_progress(execution_id, task_id, task_name, value):
        captured_progress.append(value)

    async def run():
        from flux.tasks.ai.gemini import build_gemini_agent

        mock_response = MagicMock()
        mock_response.text = "Hello world!"
        mock_response.function_calls = None

        with patch("flux.tasks.ai.gemini.genai") as mock_genai:
            mock_client = MagicMock()
            mock_client.aio.models.generate_content = AsyncMock(
                return_value=mock_response,
            )
            mock_genai.Client.return_value = mock_client

            agent_task = build_gemini_agent(
                system_prompt="Test",
                model_name="gemini-2.5-flash",
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
