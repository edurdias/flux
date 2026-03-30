"""
System Tools — Selecting Specific Tools.

Demonstrates how to pick only the tools you need from system_tools(),
and combine them with your own custom tools.

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a model: ollama pull qwen2.5-coder:14b
    3. Start Ollama service: ollama serve

Usage (in-process):
    python examples/ai/system_tools_selected_tools_ollama.py

Usage (server/worker):
    flux start server
    flux start worker
    flux workflow run system_tools_selected_tools_ollama '{"instruction": "Search for TODO comments and list them"}'
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from flux import ExecutionContext, task, workflow
from flux.tasks.ai import agent, system_tools


@task
async def count_lines(path: str) -> str:
    """Count the total number of lines across all files in the workspace."""
    total = 0
    root = Path(path)
    for f in root.rglob("*"):
        if f.is_file():
            try:
                total += len(f.read_text().splitlines())
            except (OSError, UnicodeDecodeError):
                continue
    return f"Total lines across all files: {total}"


@workflow
async def system_tools_selected_tools_ollama(ctx: ExecutionContext[dict[str, Any]]):
    """
    Agent with selected system tools plus a custom tool.

    Only uses read-only tools (no write_file, edit_file, shell) for safety,
    plus a custom line-counting tool.

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

    all_tools = system_tools(workspace=workspace)
    read_only_tools = [
        t
        for t in all_tools
        if t.func.__name__
        in ("read_file", "find_files", "grep", "list_directory", "directory_tree", "file_info")
    ]

    assistant = await agent(
        "You are a code reviewer. You can only read and search files — "
        "you cannot modify them. Analyze the codebase and provide insights.",
        model="ollama/qwen2.5-coder:14b",
        name="code_reviewer",
        tools=read_only_tools + [count_lines],
        max_tool_calls=15,
    )

    answer = await assistant(instruction)

    return {
        "instruction": instruction,
        "workspace": str(workspace),
        "answer": answer,
    }


if __name__ == "__main__":  # pragma: no cover
    workspace = Path(tempfile.mkdtemp(prefix="flux_agent_"))

    (workspace / "app.py").write_text(
        "# TODO: add error handling\n"
        "def process(data):\n"
        "    result = data['key']  # TODO: validate input\n"
        "    return result\n",
    )
    (workspace / "utils.py").write_text(
        "def helper():\n" "    # TODO: implement caching\n" "    return 42\n",
    )

    print(f"Workspace: {workspace}")
    print("=" * 80)

    result = system_tools_selected_tools_ollama.run(
        {
            "instruction": "Search for all TODO comments in the codebase and summarize what needs to be done.",
            "workspace": str(workspace),
        },
    )

    if result.has_failed:
        print(f"Failed: {result.output}")
    else:
        print(f"Answer: {result.output['answer']}")
