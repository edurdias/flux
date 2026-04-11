import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from flux._task_context import _CURRENT_TASK
from flux.domain.execution_context import ExecutionContext
from flux.tasks.ai.agent_loop import run_agent_loop


def test_anthropic_streaming_emits_progress():
    captured_progress = []

    def on_progress(execution_id, task_id, task_name, value):
        captured_progress.append(value)

    async def run():
        from flux.tasks.ai.anthropic import build_anthropic_provider

        mock_stream_cm = MagicMock()

        async def mock_text_stream():
            for token in ["Hello", " world", "!"]:
                yield token

        stream_obj = MagicMock()
        stream_obj.text_stream = mock_text_stream()

        mock_stream_cm.__aenter__ = AsyncMock(return_value=stream_obj)
        mock_stream_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("flux.tasks.ai.anthropic.AsyncAnthropic") as MockAnthropic:
            mock_client = MagicMock()
            mock_client.messages.stream = MagicMock(return_value=mock_stream_cm)
            MockAnthropic.return_value = mock_client

            llm_task, formatter = build_anthropic_provider("claude-sonnet-4-20250514")

            ctx = ExecutionContext(workflow_id="wf1", workflow_namespace="default", workflow_name="test")
            ctx.set_progress_callback(on_progress)
            ec_token = ExecutionContext.set(ctx)
            task_token = _CURRENT_TASK.set(("task-1", "test_task"))
            try:
                result = await run_agent_loop(
                    llm_task=llm_task,
                    formatter=formatter,
                    system_prompt="Test",
                    instruction="Say hello",
                    stream=True,
                )
            finally:
                _CURRENT_TASK.reset(task_token)
                ExecutionContext.reset(ec_token)

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
        from flux.tasks.ai.anthropic import build_anthropic_provider

        mock_response = MagicMock()
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Hello world!"
        mock_response.content = [text_block]

        with patch("flux.tasks.ai.anthropic.AsyncAnthropic") as MockAnthropic:
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            MockAnthropic.return_value = mock_client

            llm_task, formatter = build_anthropic_provider("claude-sonnet-4-20250514")

            ctx = ExecutionContext(workflow_id="wf1", workflow_namespace="default", workflow_name="test")
            ctx.set_progress_callback(on_progress)
            ec_token = ExecutionContext.set(ctx)
            task_token = _CURRENT_TASK.set(("task-1", "test_task"))
            try:
                result = await run_agent_loop(
                    llm_task=llm_task,
                    formatter=formatter,
                    system_prompt="Test",
                    instruction="Say hello",
                    stream=False,
                )
            finally:
                _CURRENT_TASK.reset(task_token)
                ExecutionContext.reset(ec_token)

            assert result == "Hello world!"
            assert len(captured_progress) == 0

    asyncio.run(run())
