import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from flux.domain.execution_context import ExecutionContext


def test_gemini_streaming_yields_tokens():
    async def run():
        from flux.tasks.ai.gemini import build_gemini_provider

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

            _, formatter = build_gemini_provider("gemini-2.5-flash")

            messages, call_kwargs = formatter.build_messages("Test", "Say hello")

            tokens = []
            async for tok in formatter.stream(messages, call_kwargs):
                tokens.append(tok)

            assert tokens == ["Hello", " world", "!"]

    asyncio.run(run())


def test_gemini_no_streaming_returns_response():
    async def run():
        from flux.tasks.ai.gemini import build_gemini_provider

        mock_response = MagicMock()
        mock_response.text = "Hello world!"
        mock_response.function_calls = None

        with patch("flux.tasks.ai.gemini.genai") as mock_genai:
            mock_client = MagicMock()
            mock_client.aio.models.generate_content = AsyncMock(
                return_value=mock_response,
            )
            mock_genai.Client.return_value = mock_client

            llm_task, formatter = build_gemini_provider("gemini-2.5-flash")

            ctx = ExecutionContext(
                workflow_id="wf1",
                workflow_namespace="default",
                workflow_name="test",
            )
            token = ExecutionContext.set(ctx)
            try:
                messages, call_kwargs = formatter.build_messages("Test", "Say hello")
                result = await llm_task(messages, **call_kwargs)
            finally:
                ExecutionContext.reset(token)

            assert result.text == "Hello world!"
            assert result.tool_calls == []

    asyncio.run(run())
