"""
Planning Agent with failure and replanning scenarios.

Demonstrates how an agent handles tool failures, adapts its plan,
and uses dependency results from previous steps.

Key scenarios tested:
- Tool that fails intermittently (simulates API errors)
- Agent replans when a step reveals new information
- Dependency results flow between steps via enriched summaries

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a model: ollama pull mistral-small:24b
    3. Start Ollama service: ollama serve

Usage:
    python examples/ai/planning_agent_replan.py
"""

from __future__ import annotations

from typing import Any

from flux import ExecutionContext, task, workflow
from flux.tasks.ai import agent

# Simulate intermittent failures
_call_count: dict[str, int] = {}


@task
async def search_database(query: str) -> str:
    """Search the internal database for records matching the query."""
    _call_count.setdefault("search_database", 0)
    _call_count["search_database"] += 1

    # Fail on first call to simulate a transient error
    if _call_count["search_database"] == 1:
        raise ConnectionError(f"Database connection timeout for query: {query}")

    return (
        f"Database results for '{query}':\n"
        f"- Record 1: Product A, revenue $2.3M, growth 15%\n"
        f"- Record 2: Product B, revenue $1.8M, growth -3%\n"
        f"- Record 3: Product C, revenue $4.1M, growth 22%"
    )


@task
async def search_web(query: str) -> str:
    """Search the web for public information about a topic."""
    return (
        f"Web results for '{query}':\n"
        f"- Industry report: Market growing at 18% CAGR\n"
        f"- News: Company X acquired Company Y for $500M\n"
        f"- Analysis: Top 3 players control 70% market share"
    )


@task
async def analyze_data(data: str, focus: str) -> str:
    """Analyze data with a specific focus area and produce insights."""
    return (
        f"Analysis (focus: {focus}):\n"
        f"- Key finding: Strong growth in premium segment\n"
        f"- Risk: Product B declining, needs attention\n"
        f"- Opportunity: Market consolidation creates acquisition targets\n"
        f"- Recommendation: Invest in Product C's growth trajectory"
    )


@task
async def generate_report(title: str, sections: str) -> str:
    """Generate a structured report with the given title and section content."""
    return (
        f"# {title}\n\n"
        f"## Key Findings\n{sections}\n\n"
        f"## Recommendations\n"
        f"1. Accelerate Product C investment\n"
        f"2. Review Product B strategy\n"
        f"3. Explore acquisition opportunities\n"
    )


analyst = agent(
    "You are a business analyst. Always use your tools to accomplish tasks. "
    "Never describe what you would do — actually do it by calling tools. "
    "For multi-step tasks, create a plan first using create_plan, then "
    "execute each step using your tools, and mark steps done with mark_step_done. "
    "If a tool fails, retry it or adjust your plan.",
    model="ollama/mistral-small:24b",
    name="replan-analyst",
    tools=[search_database, search_web, analyze_data, generate_report],
    planning=True,
    max_tool_calls=30,
).with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=600)


@workflow
async def planning_agent_replan(ctx: ExecutionContext[dict[str, Any]]):
    """
    A planning agent that handles failures and replans.

    Input format:
    {
        "topic": "quarterly business review"
    }
    """
    # Reset call counts for each run
    _call_count.clear()

    input_data = ctx.input or {}
    topic = input_data.get("topic", "quarterly business review")

    response = await analyst(
        f"Prepare a {topic} report. You need to: "
        f"1) Gather internal data from the database "
        f"2) Gather market data from the web "
        f"3) Analyze all gathered data together "
        f"4) Generate a final report. "
        f"Create a plan with these steps. The analysis step should depend "
        f"on both data gathering steps. The report should depend on analysis.",
    )

    return {
        "topic": topic,
        "response": response,
        "tool_calls": dict(_call_count),
        "execution_id": ctx.execution_id,
    }


if __name__ == "__main__":  # pragma: no cover
    topic = "Quarterly Business Review"

    try:
        print("=" * 80)
        print("Planning Agent with Replan Demo")
        print(f"Topic: {topic}")
        print("=" * 80 + "\n")

        print("Running agent (first search_database call will fail)...\n")
        result = planning_agent_replan.run({"topic": topic})

        if result.has_failed:
            raise Exception(f"Workflow failed: {result.output}")

        output = result.output
        print(f"Topic: {output.get('topic')}")
        print(f"Tool calls: {output.get('tool_calls')}")
        print(f"Execution ID: {output.get('execution_id')}\n")
        print("-" * 80)
        print(output.get("response", ""))
        print("-" * 80)

    except Exception as e:
        print(f"Error: {e}")
        print("\nMake sure:")
        print("1. Ollama is running: ollama serve")
        print("2. Model is pulled: ollama pull mistral-small:24b")
