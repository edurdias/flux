import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from flux._task_context import _CURRENT_TASK
from flux.domain.execution_context import ExecutionContext
from flux.tasks.ai.agent_loop import run_agent_loop


def test_openai_streaming_emits_progress():
    captured_progress = []

    def on_progress(execution_id, task_id, task_name, value):
        captured_progress.append(value)

    async def run():
        from flux.tasks.ai.openai import build_openai_provider

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

            llm_task, formatter = build_openai_provider("gpt-4o")

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


def test_openai_reasoning_stream_emits_tokens():
    async def run():
        from flux.tasks.ai.openai import build_openai_provider

        async def mock_stream():
            for reasoning_token, text_token in [
                ("Step 1: ", None),
                ("analyze.", None),
                (None, "The answer."),
            ]:
                chunk = MagicMock()
                chunk.choices = [MagicMock()]
                chunk.choices[0].delta.reasoning_content = reasoning_token
                chunk.choices[0].delta.content = text_token
                chunk.choices[0].delta.tool_calls = None
                yield chunk

        with patch("flux.tasks.ai.openai.AsyncOpenAI") as MockOpenAI:
            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_stream())
            MockOpenAI.return_value = mock_client

            _, formatter = build_openai_provider("o3-mini")

            assert formatter.supports_reasoning_stream is True

            messages, call_kwargs = formatter.build_messages("Test", "Think about this")

            reasoning_tokens = []
            on_token = AsyncMock(side_effect=lambda t: reasoning_tokens.append(t))

            response = await formatter.call_with_reasoning_stream(
                messages,
                call_kwargs,
                on_reasoning_token=on_token,
            )

            assert reasoning_tokens == ["Step 1: ", "analyze."]
            assert response.text == "The answer."
            assert response.reasoning is not None
            assert response.reasoning.text == "Step 1: analyze."
            assert response.reasoning.opaque == {"reasoning_content": "Step 1: analyze."}

    asyncio.run(run())


def test_openai_no_streaming_when_disabled():
    captured_progress = []

    def on_progress(execution_id, task_id, task_name, value):
        captured_progress.append(value)

    async def run():
        from flux.tasks.ai.openai import build_openai_provider

        message = MagicMock()
        message.content = "Hello world!"
        message.tool_calls = None
        message.reasoning_content = None

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message = message

        with patch("flux.tasks.ai.openai.AsyncOpenAI") as MockOpenAI:
            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            MockOpenAI.return_value = mock_client

            llm_task, formatter = build_openai_provider("gpt-4o")

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
