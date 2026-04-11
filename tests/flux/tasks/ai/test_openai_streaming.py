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
                workflow_id="wf1", workflow_namespace="default", workflow_name="test",
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
                workflow_id="wf1", workflow_namespace="default", workflow_name="test",
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
