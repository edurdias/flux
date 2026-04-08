"""
Reasoning Agent with Chain-of-Thought using Ollama.

Demonstrates an agent that uses reasoning/thinking models to show its
chain of thought while calling tools. The thinking traces are stored
in working memory and visible in execution events.

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a reasoning model: ollama pull qwen3
    3. Start Ollama service: ollama serve

Usage (in-process):
    python examples/ai/reasoning_agent_ollama.py

Usage (server/worker):
    flux start server
    flux start worker
    flux workflow register examples/ai/reasoning_agent_ollama.py
    flux workflow run reasoning_agent '{"question": "Compare Python and Rust for building web APIs"}'
"""
from __future__ import annotations

import asyncio
from typing import Any

from flux import ExecutionContext, task, workflow
from flux.tasks.ai import agent
from flux.tasks.ai.memory import working_memory


@task
async def search_topic(topic: str) -> str:
    """Search for information about a topic. Use this to research specific subjects."""
    await asyncio.sleep(0.5)
    knowledge = {
        "python web frameworks": (
            "Python has several popular web frameworks: FastAPI (async, high performance, "
            "auto-generated docs), Django (batteries-included, ORM, admin panel), and "
            "Flask (lightweight, flexible). FastAPI is the fastest-growing for APIs."
        ),
        "rust web frameworks": (
            "Rust web frameworks include Actix Web (fastest benchmarks), Axum (built on "
            "Tokio, ergonomic), and Rocket (easy to use, macro-based). Rust web servers "
            "typically outperform Python by 10-50x in throughput."
        ),
        "python": (
            "Python is a high-level, interpreted language known for readability and a vast "
            "ecosystem. Widely used in web development, data science, AI/ML, and scripting. "
            "GIL limits true parallelism but asyncio enables concurrent I/O."
        ),
        "rust": (
            "Rust is a systems programming language focused on safety, speed, and concurrency. "
            "No garbage collector, ownership model prevents memory bugs at compile time. "
            "Growing adoption in web services, CLI tools, and infrastructure."
        ),
    }
    topic_lower = topic.lower()
    for key, value in knowledge.items():
        if key in topic_lower:
            return value
    return f"Research results for '{topic}': General information about {topic}."


@task
async def get_statistics(subject: str) -> str:
    """Get statistics and benchmarks about a technology or subject."""
    await asyncio.sleep(0.5)
    stats = {
        "python": "Python: ~30% of developers use it (Stack Overflow 2025), 400k+ PyPI packages, avg API latency 50-200ms",
        "rust": "Rust: ~13% of developers use it (Stack Overflow 2025), most admired language 8 years running, avg API latency 1-10ms",
        "web api": "Web API benchmarks (TechEmpower 2025): Rust Actix ~700k req/s, Python FastAPI ~15k req/s, Go Gin ~300k req/s",
    }
    subject_lower = subject.lower()
    for key, value in stats.items():
        if key in subject_lower:
            return value
    return f"Statistics for '{subject}': No specific benchmarks available."


@workflow
async def reasoning_agent(ctx: ExecutionContext[dict[str, Any]]):
    """
    Research assistant with reasoning/thinking enabled.

    Input format:
    {
        "question": "Your research question",
        "model": "ollama/qwen3",
        "reasoning_effort": "high"
    }
    """
    input_data = ctx.input or {}
    question = input_data.get("question", "Compare Python and Rust for web development")
    model = input_data.get("model", "ollama/qwen3")
    effort = input_data.get("reasoning_effort", "high")

    wm = working_memory(max_tokens=50_000)
    tools = [search_topic, get_statistics]

    assistant = await agent(
        "You are a research assistant. Think carefully before acting. "
        "Use search_topic to gather information and get_statistics for data. "
        "Synthesize findings into a clear, balanced comparison.",
        model=model,
        name="reasoning_agent",
        tools=tools,
        working_memory=wm,
        reasoning_effort=effort,
        max_tool_calls=10,
        stream=False,
    )

    answer = await assistant(question)

    wm_messages = wm.recall()
    reasoning_messages = [m for m in wm_messages if m["role"] == "reasoning"]

    return {
        "question": question,
        "answer": answer,
        "thinking_count": len(reasoning_messages),
        "thinking_traces": [m["content"] for m in reasoning_messages],
        "working_memory_roles": [m["role"] for m in wm_messages],
    }


if __name__ == "__main__":
    import json

    print("=" * 70)
    print("Reasoning Agent — Chain-of-Thought with Tool Calling")
    print("=" * 70)

    result = reasoning_agent.run(
        {
            "question": "Compare Python and Rust for building web APIs. "
            "Which is better for a startup building a real-time trading platform?",
            "reasoning_effort": "high",
        },
    )

    if result.has_succeeded:
        output = result.output
        print(f"\nQuestion: {output['question']}")
        print(f"\nAnswer:\n{output['answer'][:500]}")
        print(f"\nThinking traces ({output['thinking_count']}):")
        for i, trace in enumerate(output["thinking_traces"]):
            data = json.loads(trace)
            text = data.get("text", "")
            print(f"  {i + 1}. {text[:150]}...")
        print(f"\nWM roles: {output['working_memory_roles']}")
    elif result.has_failed:
        print(f"Failed: {result.output}")
