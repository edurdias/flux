"""
System Tools Agent using Flux agent() with system_tools().

This example demonstrates an autonomous coding agent that can explore a codebase,
read and edit files, run shell commands, and search for code patterns — all using
the built-in system_tools() module.

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a model that supports tools: ollama pull qwen2.5-coder:14b
    3. Start Ollama service: ollama serve

Usage (in-process):
    python examples/ai/system_tools_agent_ollama.py

Usage (server/worker):
    flux start server
    flux start worker
    flux workflow run system_tools_agent_ollama '{"instruction": "List all Python files and summarize the project structure"}'
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from flux import ExecutionContext, workflow
from flux.tasks.ai import agent, system_tools


@workflow
async def system_tools_agent_ollama(ctx: ExecutionContext[dict[str, Any]]):
    """
    Autonomous agent with shell and file system tools.

    Input format:
    {
        "instruction": "What should the agent do?",
        "workspace": "/optional/path/to/workspace"
    }
    """
    input_data = ctx.input or {}
    instruction = input_data.get("instruction")
    if not instruction:
        return {
            "error": "Missing required parameter 'instruction'",
            "execution_id": ctx.execution_id,
        }

    workspace = input_data.get("workspace", tempfile.mkdtemp(prefix="flux_agent_"))

    tools = system_tools(workspace=workspace, timeout=60)

    assistant = await agent(
        "You are an autonomous coding assistant. You have access to shell commands "
        "and file system tools. Use them to accomplish the user's request.\n\n"
        "Available tools:\n"
        "- shell: run any shell command\n"
        "- read_file, write_file, edit_file: work with files\n"
        "- find_files, grep: search the codebase\n"
        "- list_directory, directory_tree, file_info: explore the workspace\n\n"
        "Always explore before making changes. Check your work after editing.",
        model="ollama/qwen2.5-coder:14b",
        name="system_tools_agent",
        tools=tools,
        max_tool_calls=20,
    )

    answer = await assistant(instruction)

    return {
        "instruction": instruction,
        "workspace": str(workspace),
        "answer": answer,
        "execution_id": ctx.execution_id,
    }


if __name__ == "__main__":  # pragma: no cover
    workspace = Path(tempfile.mkdtemp(prefix="flux_agent_"))

    # Create some sample files for the agent to work with
    (workspace / "hello.py").write_text('def greet(name):\n    return f"Hello, {name}!"\n')
    (workspace / "README.md").write_text("# Sample Project\n\nA demo project for testing.\n")
    (workspace / "src").mkdir()
    (workspace / "src" / "utils.py").write_text("def add(a, b):\n    return a + b\n")

    print(f"Workspace: {workspace}")
    print("=" * 80)

    instructions = [
        "Explore the workspace and describe what you find.",
        "Add type hints to all Python files in this project.",
    ]

    for instruction in instructions:
        try:
            print(f"\nInstruction: {instruction}")
            print("-" * 80)

            result = system_tools_agent_ollama.run(
                {
                    "instruction": instruction,
                    "workspace": str(workspace),
                },
            )

            if result.has_failed:
                raise Exception(f"Workflow failed: {result.output}")

            print(f"\nAnswer: {result.output['answer']}\n")

        except Exception as e:
            print(f"Error: {e}")
            print("Make sure Ollama is running: ollama serve")
            print("And model is pulled: ollama pull qwen2.5-coder:14b\n")
