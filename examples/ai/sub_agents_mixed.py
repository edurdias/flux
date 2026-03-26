"""
Mixed Sub-Agents Example.

Demonstrates a parent agent with local agents, workflow agents, and skills
all composed together.

Prerequisites:
    1. Install Ollama and pull a model: ollama pull llama3.2
    2. A running Flux server with a 'deploy_pipeline' workflow (for workflow agent)
    3. A ./skills directory with skill definitions (optional)

Usage:
    flux workflow run sub_agents_mixed '{
        "pr_number": 42,
        "code": "def hello(): return \\"world\\""
    }'
"""

from __future__ import annotations

from flux import ExecutionContext, task, workflow
from flux.tasks.ai import agent, workflow_agent


@task
async def search_web(query: str) -> str:
    """Search the web for information."""
    return f"Search results for: {query}\n- Found relevant documentation and examples"


@task
async def run_tests(code: str) -> str:
    """Run test suite against code."""
    return f"All tests passed for code snippet ({len(code)} chars)"


researcher = agent(
    "You are a thorough research specialist.",
    model="ollama/llama3.2",
    name="researcher",
    description="Deep research using web sources. Delegate when "
    "gathering and synthesizing information from multiple sources.",
    tools=[search_web],
)

reviewer = agent(
    "You are a code review expert focused on security and performance.",
    model="ollama/llama3.2",
    name="reviewer",
    description="Code review with security and performance analysis. "
    "Delegate when code needs expert review.",
    tools=[run_tests],
)

deployer = workflow_agent(
    name="deployer",
    description="Handles deployment pipelines. Delegate for deploy, "
    "rollback, or release operations. May pause for approval.",
    workflow="deploy_pipeline",
)

manager = agent(
    "You are a senior engineering manager. You coordinate your team to "
    "review, test, and deploy changes. For each PR:\n"
    "1. Have the researcher gather context about the changes\n"
    "2. Have the reviewer perform a code review\n"
    "3. If everything looks good, have the deployer handle deployment\n"
    "4. Summarize all findings in a final report",
    model="ollama/llama3.2",
    agents=[researcher, reviewer, deployer],
)


@workflow
async def sub_agents_mixed(ctx: ExecutionContext):
    """Coordinate local agents, workflow agents, and skills for a release."""
    raw = ctx.input or {}
    pr_number = raw.get("pr_number", 1)
    code = raw.get("code", "# no code provided")
    return await manager(
        f"Handle release for PR #{pr_number}. Code to review:\n```\n{code}\n```",
    )


if __name__ == "__main__":  # pragma: no cover
    result = sub_agents_mixed.run(
        {
            "pr_number": 42,
            "code": 'def hello(): return "world"',
        },
    )
    if result.has_succeeded:
        print(result.output)
    else:
        print(f"Failed: {result.output}")
