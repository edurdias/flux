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


def test_gemini_reasoning_stream_emits_tokens():
    async def run():
        from flux.tasks.ai.gemini import build_gemini_provider

        chunk1 = MagicMock()
        thought_part = MagicMock()
        thought_part.thought = True
        thought_part.text = "Reasoning step 1. "
        thought_part.function_call = None
        chunk1.candidates = [MagicMock()]
        chunk1.candidates[0].content.parts = [thought_part]
        chunk1.text = None

        chunk2 = MagicMock()
        thought_part2 = MagicMock()
        thought_part2.thought = True
        thought_part2.text = "Reasoning step 2."
        thought_part2.function_call = None
        chunk2.candidates = [MagicMock()]
        chunk2.candidates[0].content.parts = [thought_part2]
        chunk2.text = None

        chunk3 = MagicMock()
        text_part = MagicMock()
        text_part.thought = False
        text_part.text = "The answer."
        text_part.function_call = None
        chunk3.candidates = [MagicMock()]
        chunk3.candidates[0].content.parts = [text_part]
        chunk3.text = "The answer."

        async def mock_stream():
            for c in [chunk1, chunk2, chunk3]:
                yield c

        with patch("flux.tasks.ai.gemini.genai") as mock_genai:
            mock_client = MagicMock()
            mock_client.aio.models.generate_content_stream = AsyncMock(
                return_value=mock_stream(),
            )
            mock_genai.Client.return_value = mock_client

            _, formatter = build_gemini_provider("gemini-2.5-flash")

            assert formatter.supports_reasoning_stream is True

            messages, call_kwargs = formatter.build_messages("Test", "Think about this")

            reasoning_tokens = []
            on_token = AsyncMock(side_effect=lambda t: reasoning_tokens.append(t))

            response = await formatter.call_with_reasoning_stream(
                messages,
                call_kwargs,
                on_reasoning_token=on_token,
            )

            assert reasoning_tokens == ["Reasoning step 1. ", "Reasoning step 2."]
            assert response.text == "The answer."
            assert response.reasoning is not None
            assert response.reasoning.text == "Reasoning step 1. Reasoning step 2."

    asyncio.run(run())
