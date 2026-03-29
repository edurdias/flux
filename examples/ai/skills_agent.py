"""
Multi-Skill Agent using the Agent Skills Standard.

This example demonstrates how to create an agent with multiple skills that it
can activate on demand based on the task. Skills are defined as SKILL.md files
following the Agent Skills open standard (https://agentskills.io) and discovered
at runtime via SkillCatalog.

Key Features:
- Agent Skills standard compliance (portable SKILL.md files)
- Runtime skill discovery from a directory
- LLM-driven skill selection via use_skill tool
- Multi-skill stacking (activate multiple skills in one run)
- Works with any provider (Ollama, OpenAI, Anthropic)

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a model: ollama pull llama3.2
    3. Start Ollama service: ollama serve

Usage:
    # Run directly
    python examples/ai/skills_agent.py

    # Register and run via CLI
    flux workflow register examples/ai/skills_agent.py
    flux workflow run skills_agent_ollama '{"topic": "quantum computing"}'

    # Use a different model
    flux workflow run skills_agent_ollama '{"topic": "AI agents", "model": "llama3"}'
"""

from __future__ import annotations

import os
from typing import Any

from flux import ExecutionContext, task, workflow
from flux.tasks.ai import SkillCatalog, agent

try:
    SKILLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")
except NameError:
    SKILLS_DIR = os.path.join("examples", "ai", "skills")


@task
async def search_web(query: str) -> str:
    """Search the web and return relevant results for a query."""
    return (
        f"Results for '{query}':\n"
        f"1. Recent advances in {query} - Nature, 2026\n"
        f"2. A comprehensive review of {query} - IEEE, 2025\n"
        f"3. Future directions in {query} - ArXiv, 2026"
    )


catalog = SkillCatalog.from_directory(SKILLS_DIR)


@workflow
async def skills_agent_ollama(ctx: ExecutionContext[dict[str, Any]]):
    """
    A multi-skill agent that activates skills on demand.

    The agent has access to multiple skills (researcher, summarizer) and
    chooses which to activate based on the user's request.

    Input format:
    {
        "topic": "quantum computing",
        "model": "llama3.2"
    }

    Returns:
        Dictionary with the topic, response, and execution metadata.
    """
    input_data = ctx.input or {}

    topic = input_data.get("topic")
    if not topic:
        return {
            "error": "Missing required parameter 'topic'",
            "execution_id": ctx.execution_id,
        }

    assistant = await agent(
        "You are a helpful research assistant. Use your skills to complete tasks effectively.",
        model="ollama/llama3.2",
        name="skills-assistant",
        tools=[search_web],
        skills=catalog,
    )
    response = await assistant(f"Research the topic: {topic}")

    return {
        "topic": topic,
        "response": response,
        "skills_available": [s.name for s in catalog.list()],
        "execution_id": ctx.execution_id,
    }


if __name__ == "__main__":  # pragma: no cover
    topic = "The Future of AI Agents"

    try:
        print("=" * 80)
        print("Multi-Skill Agent Demo (Agent Skills Standard + Ollama)")
        print(f"Topic: {topic}")
        print(f"Skills: {[s.name for s in catalog.list()]}")
        print("=" * 80 + "\n")

        print("Running agent with skill activation...\n")
        result = skills_agent_ollama.run({"topic": topic})

        if result.has_failed:
            raise Exception(f"Workflow failed: {result.output}")

        output = result.output
        print(f"Topic: {output.get('topic')}")
        print(f"Skills available: {output.get('skills_available')}")
        print(f"Execution ID: {output.get('execution_id')}\n")
        print("-" * 80)
        print(output.get("response", ""))
        print("-" * 80)

    except Exception as e:
        print(f"Error: {e}")
        print("\nMake sure:")
        print("1. Ollama is running: ollama serve")
        print("2. Model is pulled: ollama pull llama3.2")
