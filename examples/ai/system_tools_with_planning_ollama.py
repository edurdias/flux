"""
System Tools Agent with Planning.

Combines system_tools() with agent planning to break complex tasks into
structured steps with dependency tracking.

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a model: ollama pull qwen2.5-coder:14b
    3. Start Ollama service: ollama serve

Usage (in-process):
    python examples/ai/system_tools_with_planning_ollama.py

Usage (server/worker):
    flux start server
    flux start worker
    flux workflow run system_tools_with_planning_ollama '{"instruction": "Create a Python calculator package with add, subtract, multiply, divide functions and tests"}'
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from flux import ExecutionContext, workflow
from flux.tasks.ai import agent, system_tools


@workflow
async def system_tools_with_planning_ollama(ctx: ExecutionContext[dict[str, Any]]):
    """
    Autonomous agent with planning and system tools.

    The agent creates a structured plan before executing complex tasks,
    tracking progress through each step.

    Input format:
    {
        "instruction": "What should the agent do?",
        "workspace": "/optional/path/to/workspace"
    }
    """
    input_data = ctx.input or {}
    instruction = input_data.get("instruction")
    if not instruction:
        return {"error": "Missing required parameter 'instruction'"}

    workspace = input_data.get("workspace", tempfile.mkdtemp(prefix="flux_agent_"))
    tools = system_tools(workspace=workspace, timeout=60)

    assistant = await agent(
        "You are an autonomous coding assistant. For complex tasks, create a plan "
        "first, then execute each step. Use your tools to write code, run tests, "
        "and verify your work.",
        model="ollama/qwen2.5-coder:14b",
        name="planning_coding_agent",
        tools=tools,
        planning=True,
        max_tool_calls=30,
    )

    answer = await assistant(instruction)

    return {
        "instruction": instruction,
        "workspace": str(workspace),
        "answer": answer,
    }


if __name__ == "__main__":  # pragma: no cover
    workspace = Path(tempfile.mkdtemp(prefix="flux_agent_"))

    print(f"Workspace: {workspace}")
    print("=" * 80)

    result = system_tools_with_planning_ollama.run(
        {
            "instruction": (
                "Create a Python calculator package with add, subtract, multiply, "
                "and divide functions. Include proper error handling for division by "
                "zero. Write unit tests for all functions and run them."
            ),
            "workspace": str(workspace),
        },
    )

    if result.has_failed:
        print(f"Failed: {result.output}")
    else:
        print(f"Answer: {result.output['answer']}")
