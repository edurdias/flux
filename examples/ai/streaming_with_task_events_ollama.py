"""
Streaming with Task Events - Showcase Flux's Event-Based Execution.

This example demonstrates how Flux's automatic task event generation can be used
to stream LLM tokens through the event system. Each token (or token batch) is
processed by a task, which automatically creates TASK_STARTED and TASK_COMPLETED
events that can be consumed via Server-Sent Events (SSE).

Key Concepts Demonstrated:
- Automatic event generation from tasks
- Event-based execution architecture
- Real-time streaming via SSE endpoints
- Distributed execution with event tracking

How It Works:
1. LLM generates tokens in real-time (streaming)
2. Each token batch is passed to a Flux task
3. Flux automatically creates events for each task execution
4. Events are persisted via checkpoint mechanism
5. Clients consume events via SSE endpoint in real-time

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a model: ollama pull llama3.2
    3. Start Ollama service: ollama serve

Usage (CLI):
    flux workflow run streaming_with_task_events_ollama '{"prompt": "Explain quantum computing in 3 sentences."}'

Usage (HTTP with SSE):
    curl -N http://localhost:8000/workflows/streaming_with_task_events_ollama/run/stream?detailed=true \\
      -H "Content-Type: application/json" \\
      -d '{"prompt": "Explain quantum computing in 3 sentences."}'

SSE Event Structure:
    You'll see events like:
    - TASK_STARTED: {"name": "process_token_batch", "value": {...}}
    - TASK_COMPLETED: {"name": "process_token_batch", "value": {"tokens": "Hello", "position": 0}}

For a Python SSE client example, see: examples/ai/stream_event_client.py
"""

from __future__ import annotations

import time
from typing import Any

from ollama import AsyncClient

from flux import ExecutionContext, task, workflow


@task
async def process_token_batch(
    tokens: str,
    batch_number: int,
    total_tokens: int,
) -> dict[str, Any]:
    """
    Process a batch of streaming tokens.

    This task automatically generates Flux events:
    - TASK_STARTED: Signals batch processing has begun
    - TASK_COMPLETED: Contains the processed batch data in event.value

    These events can be consumed in real-time via the SSE endpoint,
    allowing clients to reconstruct the streaming response.

    Args:
        tokens: The token string for this batch
        batch_number: Sequential batch identifier
        total_tokens: Total tokens processed so far

    Returns:
        Dictionary with batch metadata that will be stored in TASK_COMPLETED event
    """
    return {
        "tokens": tokens,
        "batch_number": batch_number,
        "total_tokens": total_tokens,
        "timestamp": time.time(),
    }


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=120)
async def stream_llm_with_task_events(
    prompt: str,
    system_prompt: str,
    model: str,
    ollama_url: str,
    batch_size: int,
) -> dict[str, Any]:
    """
    Stream LLM response and emit events for each token batch.

    This demonstrates Flux's event-based execution:
    1. Streams tokens from LLM
    2. Batches tokens for efficiency
    3. Passes each batch to process_token_batch task
    4. Each task call generates TASK_STARTED + TASK_COMPLETED events
    5. Events are automatically persisted and available via SSE

    Args:
        prompt: User's prompt
        system_prompt: System prompt for the LLM
        model: Ollama model to use
        ollama_url: Ollama server URL
        batch_size: Number of tokens per batch (affects event frequency)

    Returns:
        Complete response with streaming metadata
    """
    try:
        client = AsyncClient(host=ollama_url)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        # Track streaming metrics
        start_time = time.time()
        complete_response = ""
        token_buffer = ""
        total_tokens = 0
        batch_count = 0

        print("\n" + "=" * 60)
        print("STREAMING WITH TASK EVENTS")
        print("=" * 60)
        print(
            f"Each batch of {batch_size} tokens will generate Flux task events",
        )
        print("Monitor via: GET /workflows/<name>/executions/<id>?detailed=true")
        print("Stream via: POST /workflows/<name>/run/stream?detailed=true")
        print("=" * 60 + "\n")

        # Stream tokens from LLM
        async for chunk in await client.chat(model=model, messages=messages, stream=True):
            token = chunk["message"]["content"]
            complete_response += token
            token_buffer += token
            total_tokens += 1

            # Print to stdout for CLI demonstration
            print(token, end="", flush=True)

            # Emit task event when batch is ready
            if len(token_buffer) >= batch_size:
                # This task call automatically creates TASK_STARTED + TASK_COMPLETED events
                await process_token_batch(
                    tokens=token_buffer,
                    batch_number=batch_count,
                    total_tokens=total_tokens,
                )
                batch_count += 1
                token_buffer = ""

        # Process remaining tokens
        if token_buffer:
            await process_token_batch(
                tokens=token_buffer,
                batch_number=batch_count,
                total_tokens=total_tokens,
            )
            batch_count += 1

        print("\n" + "=" * 60)

        generation_time = time.time() - start_time
        tokens_per_second = total_tokens / generation_time if generation_time > 0 else 0

        return {
            "response": complete_response,
            "streaming_metrics": {
                "total_tokens": total_tokens,
                "total_batches": batch_count,
                "batch_size": batch_size,
                "generation_time_seconds": round(generation_time, 2),
                "tokens_per_second": round(tokens_per_second, 2),
            },
            "event_info": {
                "task_events_generated": batch_count * 2,  # STARTED + COMPLETED per batch
                "events_per_batch": 2,
                "message": "Each batch generated TASK_STARTED and TASK_COMPLETED events",
            },
        }

    except Exception as e:
        raise RuntimeError(
            f"Failed to call Ollama API: {str(e)}. "
            "Make sure Ollama is running (ollama serve) and the model is available.",
        ) from e


@workflow.with_options(name="streaming_with_task_events_ollama")
async def streaming_with_task_events_ollama(ctx: ExecutionContext[dict[str, Any]]):
    """
    Showcase Flux's event-based execution with LLM streaming.

    This workflow demonstrates how Flux's automatic task event generation
    can be used to stream data through the event system. Each token batch
    becomes a task execution, creating traceable events that can be consumed
    in real-time via SSE.

    Input format:
    {
        "prompt": "Your prompt here",  # Required
        "system_prompt": "Optional system prompt",
        "model": "llama3.2",  # Optional: model name
        "batch_size": 5,  # Optional: tokens per batch (default: 5)
        "ollama_url": "http://localhost:11434"  # Optional
    }

    Returns:
    {
        "response": "Generated text...",
        "streaming_metrics": {
            "total_tokens": 150,
            "total_batches": 30,
            "batch_size": 5,
            "generation_time_seconds": 8.5,
            "tokens_per_second": 17.6
        },
        "event_info": {
            "task_events_generated": 60,  # STARTED + COMPLETED per batch
            "message": "Each batch generated TASK_STARTED and TASK_COMPLETED events"
        },
        "execution_id": "..."
    }

    Event Consumption:
    - HTTP GET: /workflows/{name}/executions/{id}?detailed=true
    - SSE Stream: /workflows/{name}/run/stream?detailed=true
    """
    # Get configuration from input
    input_data = ctx.input or {}
    prompt = input_data.get("prompt")
    system_prompt = input_data.get(
        "system_prompt",
        "You are a helpful AI assistant. Provide clear, concise explanations.",
    )
    model = input_data.get("model", "llama3.2")
    batch_size = input_data.get("batch_size", 5)  # Balance latency vs event volume
    ollama_url = input_data.get("ollama_url", "http://localhost:11434")

    # Validate required inputs
    if not prompt:
        return {
            "error": "No prompt provided in input",
            "execution_id": ctx.execution_id,
        }

    # Stream LLM response with task-based event generation
    result = await stream_llm_with_task_events(
        prompt=prompt,
        system_prompt=system_prompt,
        model=model,
        ollama_url=ollama_url,
        batch_size=batch_size,
    )

    return {
        **result,
        "execution_id": ctx.execution_id,
        "how_to_consume_events": {
            "http_api": f"GET /workflows/streaming_with_task_events_ollama/executions/{ctx.execution_id}?detailed=true",
            "sse_stream": "POST /workflows/streaming_with_task_events_ollama/run/stream?detailed=true",
            "python_client": "See examples/ai/stream_event_client.py",
        },
    }


if __name__ == "__main__":  # pragma: no cover
    import asyncio

    async def run_example():
        """Run example demonstrating task-based event streaming."""
        prompt = "Explain how machine learning models learn from data in 3 paragraphs."

        print("\n" + "=" * 60)
        print("TASK-BASED EVENT STREAMING DEMO")
        print("=" * 60)
        print(f"\nPrompt: {prompt}\n")
        print("This example demonstrates Flux's event-based execution:")
        print("  - Each token batch is processed by a Flux task")
        print("  - Tasks automatically generate TASK_STARTED + TASK_COMPLETED events")
        print("  - Events can be consumed in real-time via SSE")
        print("=" * 60)

        try:
            result = streaming_with_task_events_ollama.run(
                {
                    "prompt": prompt,
                    "model": "llama3.2",
                    "batch_size": 10,  # Larger batches = fewer events
                },
            )

            if result.has_failed:
                raise Exception(f"Workflow failed: {result.output}")

            print("\n\n" + "=" * 60)
            print("STREAMING COMPLETE")
            print("=" * 60)
            print("\nMetrics:")
            print(f"  Total tokens: {result.output['streaming_metrics']['total_tokens']}")
            print(
                f"  Total batches: {result.output['streaming_metrics']['total_batches']}",
            )
            print(
                f"  Batch size: {result.output['streaming_metrics']['batch_size']} tokens",
            )
            print(
                f"  Generation time: {result.output['streaming_metrics']['generation_time_seconds']}s",
            )
            print(
                f"  Tokens/second: {result.output['streaming_metrics']['tokens_per_second']}",
            )

            print("\nEvent Info:")
            print(
                f"  Task events generated: {result.output['event_info']['task_events_generated']}",
            )
            print(
                f"  Events per batch: {result.output['event_info']['events_per_batch']} (STARTED + COMPLETED)",
            )

            print("\nHow to consume events in real-time:")
            print(f"  HTTP API: {result.output['how_to_consume_events']['http_api']}")
            print(f"  SSE Stream: {result.output['how_to_consume_events']['sse_stream']}")
            print(
                f"  Python Client: {result.output['how_to_consume_events']['python_client']}",
            )

        except Exception as e:
            print(f"\nError: {e}")
            print("\nMake sure:")
            print("  1. Ollama is running: ollama serve")
            print("  2. You have pulled a model: ollama pull llama3.2")
            print("  3. Flux server and worker are running")

    asyncio.run(run_example())
