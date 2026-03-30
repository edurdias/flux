"""
System Tools Agent with Long-term Memory.

Combines system_tools() with long-term memory so the agent remembers
facts across workflow executions — useful for multi-session projects.

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a model: ollama pull qwen2.5-coder:14b
    3. Start Ollama service: ollama serve

Usage (in-process):
    python examples/ai/system_tools_with_memory_ollama.py

Usage (server/worker):
    flux start server
    flux start worker
    flux workflow run system_tools_with_memory_ollama '{"instruction": "Explore the project and remember its structure"}'
    flux workflow run system_tools_with_memory_ollama '{"instruction": "What did you learn about this project last time?"}'
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from flux import ExecutionContext, workflow
from flux.tasks.ai import agent, long_term_memory, sqlite, system_tools
from flux.tasks.ai.memory import WorkingMemory


@workflow
async def system_tools_with_memory_ollama(ctx: ExecutionContext[dict[str, Any]]):
    """
    Autonomous agent with persistent memory and system tools.

    The agent remembers facts from previous runs and can recall them
    in future sessions.

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

    ltm = long_term_memory(
        provider=sqlite(f"{workspace}/.agent_memory.db"),
        scope="project",
    )

    assistant = await agent(
        "You are an autonomous coding assistant with persistent memory. "
        "Use your memory to store important facts about the project you discover. "
        "Recall stored facts to maintain context across sessions.",
        model="ollama/qwen2.5-coder:14b",
        name="memory_coding_agent",
        tools=tools,
        working_memory=WorkingMemory(window=10),
        long_term_memory=ltm,
        max_tool_calls=20,
    )

    answer = await assistant(instruction)

    return {
        "instruction": instruction,
        "workspace": str(workspace),
        "answer": answer,
    }


if __name__ == "__main__":  # pragma: no cover
    workspace = Path(tempfile.mkdtemp(prefix="flux_agent_"))

    (workspace / "main.py").write_text(
        "import sys\n\ndef main():\n    print('Hello from the app')\n\n"
        "if __name__ == '__main__':\n    main()\n",
    )
    (workspace / "config.yaml").write_text("app:\n  name: demo\n  debug: true\n")

    print(f"Workspace: {workspace}")
    print("=" * 80)

    instructions = [
        "Explore the project and remember what you find about its structure and purpose.",
        "Based on what you remember, add a logging module to this project.",
    ]

    for instruction in instructions:
        print(f"\nInstruction: {instruction}")
        print("-" * 80)

        result = system_tools_with_memory_ollama.run(
            {
                "instruction": instruction,
                "workspace": str(workspace),
            },
        )

        if result.has_failed:
            print(f"Failed: {result.output}")
        else:
            print(f"Answer: {result.output['answer']}")
