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

            ctx = ExecutionContext(
                workflow_id="wf1",
                workflow_namespace="default",
                workflow_name="test",
            )
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


def test_anthropic_reasoning_stream_emits_tokens():
    async def run():
        from flux.tasks.ai.anthropic import build_anthropic_provider

        thinking_delta1 = MagicMock()
        thinking_delta1.type = "content_block_delta"
        thinking_delta1.delta = MagicMock(type="thinking_delta", thinking="Let me ")

        thinking_delta2 = MagicMock()
        thinking_delta2.type = "content_block_delta"
        thinking_delta2.delta = MagicMock(type="thinking_delta", thinking="think...")

        text_delta = MagicMock()
        text_delta.type = "content_block_delta"
        text_delta.delta = MagicMock(type="text_delta")

        final_message = MagicMock()
        thinking_block = MagicMock()
        thinking_block.type = "thinking"
        thinking_block.thinking = "Let me think..."
        thinking_block.signature = "sig_abc"
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "The answer."
        final_message.content = [thinking_block, text_block]

        mock_stream_cm = MagicMock()

        async def mock_event_stream():
            for event in [thinking_delta1, thinking_delta2, text_delta]:
                yield event

        stream_obj = MagicMock()
        stream_obj.__aiter__ = lambda self: mock_event_stream()
        stream_obj.get_final_message = AsyncMock(return_value=final_message)

        mock_stream_cm.__aenter__ = AsyncMock(return_value=stream_obj)
        mock_stream_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("flux.tasks.ai.anthropic.AsyncAnthropic") as MockAnthropic:
            mock_client = MagicMock()
            mock_client.messages.stream = MagicMock(return_value=mock_stream_cm)
            MockAnthropic.return_value = mock_client

            _, formatter = build_anthropic_provider("claude-sonnet-4-20250514")

            assert formatter.supports_reasoning_stream is True

            messages, call_kwargs = formatter.build_messages("Test", "Think about this")

            reasoning_tokens = []
            on_token = AsyncMock(side_effect=lambda t: reasoning_tokens.append(t))

            response = await formatter.call_with_reasoning_stream(
                messages,
                call_kwargs,
                on_reasoning_token=on_token,
            )

            assert reasoning_tokens == ["Let me ", "think..."]
            assert response.text == "The answer."
            assert response.reasoning is not None
            assert response.reasoning.text == "Let me think..."
            assert response.reasoning.opaque["type"] == "thinking"

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

            ctx = ExecutionContext(
                workflow_id="wf1",
                workflow_namespace="default",
                workflow_name="test",
            )
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
