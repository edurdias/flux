"""
Local Sub-Agents Example.

Demonstrates two local sub-agents (researcher + reviewer) coordinated by a
parent manager agent using the `agents` parameter.

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a model: ollama pull mistral-small:24b
    3. Start Ollama service: ollama serve

Usage:
    flux workflow run sub_agents_local '{"topic": "async programming in Python"}'
"""

from __future__ import annotations

from flux import ExecutionContext, task, workflow
from flux.tasks.ai import agent


@task
async def search_web(query: str) -> str:
    """Search the web for information."""
    return (
        f"Search results for: {query}\n"
        f"- Result 1: Overview of {query}\n"
        f"- Result 2: Best practices for {query}"
    )


@workflow
async def sub_agents_local(ctx: ExecutionContext):
    """Coordinate local sub-agents to research and review a topic."""
    topic = ctx.input.get("topic", "distributed systems") if ctx.input else "distributed systems"

    researcher = await agent(
        "You are a thorough research specialist. When given a topic, use the "
        "search_web tool to gather information, then synthesize your findings "
        "into a clear summary.",
        model="ollama/qwen3",
        name="researcher",
        description="Deep research using web sources.",
        tools=[search_web],
    )

    reviewer = await agent(
        "You are a technical reviewer. Evaluate the quality, accuracy, and "
        "completeness of research summaries. Provide constructive feedback.",
        model="ollama/qwen3",
        name="reviewer",
        description="Reviews and critiques research output for quality and completeness.",
    )

    manager = await agent(
        "You are a senior engineering manager. When given a topic:\n"
        "1. Delegate research to the 'researcher' agent\n"
        "2. Send the research output to the 'reviewer' agent for feedback\n"
        "3. Provide a final summary combining both perspectives",
        model="ollama/qwen3",
        name="manager",
        agents=[researcher, reviewer],
        max_tool_calls=10,
    )

    return await manager(f"Research and review the topic: {topic}")


if __name__ == "__main__":  # pragma: no cover
    result = sub_agents_local.run({"topic": "async programming in Python"})
    if result.has_succeeded:
        print(result.output)
    else:
        print(f"Failed: {result.output}")
