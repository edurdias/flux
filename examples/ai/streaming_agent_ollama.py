"""
Streaming Response Agent using Ollama (Local LLM).

This example demonstrates real-time token streaming for better user experience.
Instead of waiting for the entire response, tokens are streamed as they're generated,
providing immediate feedback and a more interactive feel.

Use cases:
- Long-form content generation (essays, reports, stories)
- Real-time translations
- Interactive chatbots with immediate feedback
- Code generation with live preview
- Any scenario where perceived latency matters

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a model: ollama pull llama3.2
    3. Start Ollama service: ollama serve

Usage:
    # Generate streaming content
    flux workflow run streaming_agent_ollama '{"prompt": "Write a detailed explanation of how neural networks work", "stream": true}'

    # Compare with non-streaming (default)
    flux workflow run streaming_agent_ollama '{"prompt": "Write a detailed explanation of how neural networks work"}'

    # Use different model
    flux workflow run streaming_agent_ollama '{"prompt": "Explain quantum computing", "model": "qwen2.5:3b", "stream": true}'
"""

from __future__ import annotations

import time
from typing import Any

from ollama import AsyncClient

from flux import ExecutionContext, task, workflow


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=120)
async def generate_streaming_response(
    prompt: str,
    system_prompt: str,
    model: str,
    ollama_url: str,
) -> tuple[str, float, int]:
    """
    Generate a streaming response from Ollama.

    This task streams tokens as they're generated, providing real-time feedback.
    In a production environment, you would typically yield/stream these tokens
    to the client for display.

    Args:
        prompt: User's prompt
        system_prompt: System prompt for the LLM
        model: Ollama model to use
        ollama_url: Ollama server URL

    Returns:
        Tuple of (complete_response, generation_time, token_count)
    """
    try:
        client = AsyncClient(host=ollama_url)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        # Track metrics
        start_time = time.time()
        complete_response = ""
        token_count = 0

        # Stream the response
        print("\n" + "=" * 60)
        print("STREAMING RESPONSE:")
        print("=" * 60)

        async for chunk in await client.chat(model=model, messages=messages, stream=True):
            # Extract the token from the chunk
            token = chunk["message"]["content"]
            complete_response += token
            token_count += 1

            # Print token in real-time (in production, you'd send this to client)
            print(token, end="", flush=True)

        print("\n" + "=" * 60)

        generation_time = time.time() - start_time

        return complete_response, generation_time, token_count

    except Exception as e:
        raise RuntimeError(
            f"Failed to call Ollama API: {str(e)}. "
            "Make sure Ollama is running (ollama serve) and the model is available.",
        ) from e


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=120)
async def generate_non_streaming_response(
    prompt: str,
    system_prompt: str,
    model: str,
    ollama_url: str,
) -> tuple[str, float]:
    """
    Generate a non-streaming response from Ollama for comparison.

    Args:
        prompt: User's prompt
        system_prompt: System prompt for the LLM
        model: Ollama model to use
        ollama_url: Ollama server URL

    Returns:
        Tuple of (complete_response, generation_time)
    """
    try:
        client = AsyncClient(host=ollama_url)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        # Track metrics
        start_time = time.time()

        # Get complete response at once
        response = await client.chat(model=model, messages=messages)
        complete_response = response["message"]["content"]

        generation_time = time.time() - start_time

        print("\n" + "=" * 60)
        print("NON-STREAMING RESPONSE:")
        print("=" * 60)
        print(complete_response)
        print("=" * 60)

        return complete_response, generation_time

    except Exception as e:
        raise RuntimeError(
            f"Failed to call Ollama API: {str(e)}. "
            "Make sure Ollama is running (ollama serve) and the model is available.",
        ) from e


@workflow.with_options(name="streaming_agent_ollama")
async def streaming_agent_ollama(ctx: ExecutionContext[dict[str, Any]]):
    """
    An AI agent that demonstrates streaming vs non-streaming responses.

    This workflow shows the performance and UX benefits of streaming tokens
    in real-time rather than waiting for the complete response.

    Input format:
    {
        "prompt": "Your prompt here",  # Required
        "stream": true,  # Optional: enable streaming (default: true)
        "system_prompt": "Optional system prompt",
        "model": "llama3.2",  # Optional: model name
        "ollama_url": "http://localhost:11434"  # Optional: Ollama URL
    }

    Returns:
    {
        "response": "Generated text...",
        "mode": "streaming" | "non-streaming",
        "generation_time_seconds": 12.34,
        "tokens_generated": 256,  # Only for streaming mode
        "tokens_per_second": 20.8,  # Only for streaming mode
        "model": "llama3.2"
    }
    """
    # Get configuration from input
    input_data = ctx.input or {}
    prompt = input_data.get("prompt")
    stream = input_data.get("stream", True)
    system_prompt = input_data.get(
        "system_prompt",
        "You are a helpful AI assistant. Provide clear, detailed explanations.",
    )
    model = input_data.get("model", "llama3.2")
    ollama_url = input_data.get("ollama_url", "http://localhost:11434")

    # Validate required inputs
    if not prompt:
        return {
            "error": "No prompt provided in input",
            "execution_id": ctx.execution_id,
        }

    # Generate response based on streaming preference
    if stream:
        response, generation_time, token_count = await generate_streaming_response(
            prompt=prompt,
            system_prompt=system_prompt,
            model=model,
            ollama_url=ollama_url,
        )

        tokens_per_second = token_count / generation_time if generation_time > 0 else 0

        return {
            "response": response,
            "mode": "streaming",
            "generation_time_seconds": round(generation_time, 2),
            "tokens_generated": token_count,
            "tokens_per_second": round(tokens_per_second, 2),
            "model": model,
            "execution_id": ctx.execution_id,
        }
    else:
        response, generation_time = await generate_non_streaming_response(
            prompt=prompt,
            system_prompt=system_prompt,
            model=model,
            ollama_url=ollama_url,
        )

        return {
            "response": response,
            "mode": "non-streaming",
            "generation_time_seconds": round(generation_time, 2),
            "model": model,
            "execution_id": ctx.execution_id,
        }


if __name__ == "__main__":  # pragma: no cover
    import asyncio

    async def run_example():
        """Run example demonstrating both streaming and non-streaming modes."""
        prompt = "Explain how neural networks learn from data in 3 paragraphs."

        print("\n" + "=" * 60)
        print("STREAMING AGENT DEMO")
        print("=" * 60)
        print(f"\nPrompt: {prompt}\n")

        try:
            # Test streaming mode
            print("\n" + "=" * 60)
            print("MODE 1: STREAMING (Real-time token generation)")
            print("=" * 60)

            result_streaming = streaming_agent_ollama.run(
                {
                    "prompt": prompt,
                    "stream": True,
                    "model": "llama3.2",
                },
            )

            if result_streaming.has_failed:
                raise Exception(f"Streaming workflow failed: {result_streaming.output}")

            print("\n\nStreaming Metrics:")
            print(f"  - Generation time: {result_streaming.output['generation_time_seconds']}s")
            print(f"  - Tokens generated: {result_streaming.output['tokens_generated']}")
            print(f"  - Tokens per second: {result_streaming.output['tokens_per_second']}")

            # Test non-streaming mode for comparison
            print("\n\n" + "=" * 60)
            print("MODE 2: NON-STREAMING (Wait for complete response)")
            print("=" * 60)

            result_non_streaming = streaming_agent_ollama.run(
                {
                    "prompt": prompt,
                    "stream": False,
                    "model": "llama3.2",
                },
            )

            if result_non_streaming.has_failed:
                raise Exception(f"Non-streaming workflow failed: {result_non_streaming.output}")

            print("\n\nNon-Streaming Metrics:")
            print(f"  - Generation time: {result_non_streaming.output['generation_time_seconds']}s")

            # Compare
            print("\n" + "=" * 60)
            print("COMPARISON")
            print("=" * 60)
            print(
                f"Streaming: {result_streaming.output['generation_time_seconds']}s "
                f"({result_streaming.output['tokens_per_second']} tokens/s)",
            )
            print(
                f"Non-streaming: {result_non_streaming.output['generation_time_seconds']}s "
                f"(0 tokens/s until complete)",
            )
            print("\nKey benefits of streaming:")
            print("  - Immediate feedback for users")
            print("  - Better perceived performance")
            print("  - Can start processing partial responses")
            print("  - Improved interactivity for long responses")

        except Exception as e:
            print(f"\nError: {e}")
            print("\nMake sure:")
            print("  1. Ollama is running: ollama serve")
            print("  2. You have pulled a model: ollama pull llama3.2")

    asyncio.run(run_example())
