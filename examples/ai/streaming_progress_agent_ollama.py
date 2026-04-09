"""
Streaming Agent with Task Progress.

Demonstrates how the agent() task uses the progress() primitive to stream
LLM tokens in real-time. When run via the API with stream mode, clients
receive TASK_PROGRESS events containing each token as it is generated.

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a model: ollama pull llama3.2
    3. Start Ollama service: ollama serve

Usage:
    flux workflow run streaming_progress_agent '{"prompt": "Explain quantum computing in 3 sentences"}'

    curl -N -X POST http://localhost:8000/workflows/streaming_progress_agent/run/stream \\
        -H "Content-Type: application/json" \\
        -d '{"prompt": "Explain quantum computing in 3 sentences"}'
"""

from __future__ import annotations

from typing import Any

from flux import ExecutionContext, workflow
from flux.tasks.ai import agent


@workflow
async def streaming_progress_agent(ctx: ExecutionContext[dict[str, Any]]):
    input_data = ctx.input or {}
    prompt = input_data.get("prompt", "Hello!")
    use_streaming = input_data.get("stream", True)

    assistant = await agent(
        "You are a helpful assistant. Be concise and clear.",
        model="ollama/llama3.2",
        stream=use_streaming,
    )
    result = await assistant(prompt)

    return {
        "response": result,
        "mode": "streaming" if use_streaming else "non-streaming",
    }
