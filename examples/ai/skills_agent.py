"""Example: Multi-skill agent using the Agent Skills standard.

This example demonstrates how to create an agent with multiple skills
that it can activate on demand based on the task.

Skills are defined as SKILL.md files following the Agent Skills standard
(https://agentskills.io) and discovered via SkillCatalog.

Usage:
    flux run examples/ai/skills_agent.py
"""

from flux import task, workflow
from flux.tasks.ai import SkillCatalog, agent


@task
async def search_web(query: str) -> str:
    """Search the web and return relevant results."""
    return (
        f"Results for '{query}':\n"
        f"1. Recent advances in {query} - Nature, 2026\n"
        f"2. A comprehensive review of {query} - IEEE, 2025\n"
        f"3. Future directions in {query} - ArXiv, 2026"
    )


catalog = SkillCatalog.from_directory("examples/ai/skills")

assistant = agent(
    "You are a helpful research assistant.",
    model="ollama/qwen2.5:0.5b",
    tools=[search_web],
    skills=catalog,
)


@workflow
async def research_workflow(ctx, topic: str = "quantum computing"):
    return await assistant(f"Research the topic: {topic}")
