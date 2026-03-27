"""
Planning Agent using Ollama.

Demonstrates how to create an agent with planning capabilities. The agent
creates a structured plan before tackling complex tasks, works through each
step, and tracks progress with named results.

Key Features:
- LLM-driven plan creation (the agent decides when to plan)
- Named steps with dependency tracking
- Automatic result storage for dependent steps
- Status tracking with plan summaries
- Replanning when circumstances change

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a model: ollama pull llama3.2
    3. Start Ollama service: ollama serve

Usage:
    python examples/ai/planning_agent_ollama.py
"""

from __future__ import annotations

from typing import Any

from flux import ExecutionContext, task, workflow
from flux.tasks.ai import agent


@task
async def search_web(query: str) -> str:
    """Search the web and return relevant results for a query."""
    return (
        f"Results for '{query}':\n"
        f"1. Market analysis: {query} shows 15% growth in 2026\n"
        f"2. Key players: Company A (35% share), Company B (28% share), Company C (20% share)\n"
        f"3. Emerging trend: AI integration driving innovation in {query}"
    )


@task
async def analyze_data(data: str) -> str:
    """Analyze data and produce structured insights."""
    return (
        "Analysis of provided data:\n"
        "- Primary finding: Strong market growth trajectory\n"
        "- Key insight: Top 3 players control 83% of market\n"
        "- Recommendation: Focus on AI-driven differentiation\n"
        "- Risk factor: Market consolidation may limit new entrants"
    )


@task
async def write_report(topic: str, content: str) -> str:
    """Write a formatted report on a topic with the given content."""
    return (
        f"# Market Report: {topic}\n\n"
        f"## Executive Summary\n{content}\n\n"
        f"## Conclusion\nBased on our analysis, the market presents "
        f"significant opportunities for AI-driven solutions.\n"
    )


analyst = agent(
    "You are a thorough market research analyst. "
    "For complex research tasks, create a plan to organize your work. "
    "Use your tools to gather data, analyze it, and produce reports.",
    model="ollama/mistral-small:24b",
    name="planning-analyst",
    tools=[search_web, analyze_data, write_report],
    planning=True,
    max_tool_calls=30,
).with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=600)


@workflow
async def planning_agent_ollama(ctx: ExecutionContext[dict[str, Any]]):
    """
    A planning agent that organizes complex research tasks.

    Input format:
    {
        "topic": "cloud computing market"
    }
    """
    input_data = ctx.input or {}
    topic = input_data.get("topic", "AI agent frameworks")

    response = await analyst(
        f"Research the competitive landscape for '{topic}' and produce "
        f"a comprehensive market report. This requires multiple steps: "
        f"gathering data, analyzing trends, and writing a report.",
    )

    return {
        "topic": topic,
        "response": response,
        "execution_id": ctx.execution_id,
    }


if __name__ == "__main__":  # pragma: no cover
    topic = "AI Agent Frameworks"

    try:
        print("=" * 80)
        print("Planning Agent Demo (Ollama)")
        print(f"Topic: {topic}")
        print("=" * 80 + "\n")

        print("Running agent with planning enabled...\n")
        result = planning_agent_ollama.run({"topic": topic})

        if result.has_failed:
            raise Exception(f"Workflow failed: {result.output}")

        output = result.output
        print(f"Topic: {output.get('topic')}")
        print(f"Execution ID: {output.get('execution_id')}\n")
        print("-" * 80)
        print(output.get("response", ""))
        print("-" * 80)

    except Exception as e:
        print(f"Error: {e}")
        print("\nMake sure:")
        print("1. Ollama is running: ollama serve")
        print("2. Model is pulled: ollama pull llama3.2")
