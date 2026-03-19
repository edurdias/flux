import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from flux.domain.execution_context import ExecutionContext


def test_anthropic_streaming_emits_progress():
    captured_progress = []

    def on_progress(execution_id, task_id, task_name, value):
        captured_progress.append(value)

    async def run():
        from flux.tasks.ai.anthropic import build_anthropic_agent

        mock_stream_cm = MagicMock()

        async def mock_text_stream():
            for token in ["Hello", " world", "!"]:
                yield token

        final_message = MagicMock()
        final_message.content = [MagicMock(type="text", text="Hello world!")]

        stream_obj = MagicMock()
        stream_obj.text_stream = mock_text_stream()
        stream_obj.get_final_message = MagicMock(return_value=final_message)

        mock_stream_cm.__aenter__ = AsyncMock(return_value=stream_obj)
        mock_stream_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("flux.tasks.ai.anthropic.AsyncAnthropic") as MockAnthropic:
            mock_client = MagicMock()
            mock_client.messages.stream = MagicMock(return_value=mock_stream_cm)
            MockAnthropic.return_value = mock_client

            agent_task = build_anthropic_agent(
                system_prompt="Test",
                model_name="claude-sonnet-4-20250514",
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


def test_anthropic_no_streaming_when_disabled():
    captured_progress = []

    def on_progress(execution_id, task_id, task_name, value):
        captured_progress.append(value)

    async def run():
        from flux.tasks.ai.anthropic import build_anthropic_agent

        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Hello world!")]

        with patch("flux.tasks.ai.anthropic.AsyncAnthropic") as MockAnthropic:
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            MockAnthropic.return_value = mock_client

            agent_task = build_anthropic_agent(
                system_prompt="Test",
                model_name="claude-sonnet-4-20250514",
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
