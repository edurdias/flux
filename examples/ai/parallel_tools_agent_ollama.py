"""
Parallel Tool Execution with Flux Agents.

Demonstrates how agents execute multiple tool calls concurrently when the
LLM emits them in a single response.

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a model: ollama pull mistral-small:24b
    3. Start Ollama service: ollama serve

Usage (in-process):
    python examples/ai/parallel_tools_agent_ollama.py

Usage (server/worker):
    flux start server
    flux start worker
    flux workflow run parallel_tools_agent_ollama '{"question": "Compare the weather in Tokyo, London, and New York"}'
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from flux import ExecutionContext, task, workflow
from flux.tasks.ai import agent


@task
async def search_topic(topic: str) -> str:
    """Search for information about a topic. Use this to research specific subjects."""
    await asyncio.sleep(1)
    return (
        f"Research results for '{topic}': This is a simulated search result "
        f"with key findings about {topic}."
    )


@task
async def get_statistics(subject: str) -> str:
    """Get statistics and data about a subject."""
    await asyncio.sleep(1)
    return (
        f"Statistics for '{subject}': Population data, economic indicators, "
        f"and trends related to {subject}."
    )


@task
async def check_news(topic: str) -> str:
    """Check recent news about a topic."""
    await asyncio.sleep(1)
    return f"Recent news about '{topic}': Latest developments and headlines related to {topic}."


@workflow
async def parallel_tools_agent_ollama(ctx: ExecutionContext[dict[str, Any]]):
    """
    Agent that demonstrates parallel tool execution.

    When the LLM needs multiple pieces of information, it can call several
    tools at once. Flux runs them concurrently, reducing total wait time.

    Input format:
    {
        "question": "A question that requires multiple lookups"
    }
    """
    input_data = ctx.input or {}
    question = input_data.get("question")
    if not question:
        return {"error": "Missing required parameter 'question'"}

    tools = [search_topic, get_statistics, check_news]

    assistant = await agent(
        "You are a research assistant. You MUST use your tools to answer questions. "
        "Do NOT answer from memory. Always call multiple tools at once when possible.",
        model="ollama/mistral-small:24b",
        name="parallel_researcher",
        tools=tools,
        max_tool_calls=10,
    )

    start = time.monotonic()
    answer = await assistant(question)
    elapsed = time.monotonic() - start

    return {
        "question": question,
        "answer": answer,
        "elapsed_seconds": round(elapsed, 2),
    }


if __name__ == "__main__":  # pragma: no cover
    questions = [
        "Compare Tokyo, London, and New York in terms of culture and economy.",
        "What are the latest developments in AI, quantum computing, and renewable energy?",
    ]

    for question in questions:
        print(f"\nQuestion: {question}")
        print("-" * 80)

        result = parallel_tools_agent_ollama.run({"question": question})

        if result.has_failed:
            print(f"Failed: {result.output}")
        else:
            print(f"Answer: {result.output['answer']}")
            print(f"Time: {result.output['elapsed_seconds']}s")
