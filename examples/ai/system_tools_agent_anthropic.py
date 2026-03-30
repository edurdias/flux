"""
System Tools Agent using Anthropic Claude.

Autonomous coding agent with shell and file system access, powered by Claude.

Prerequisites:
    1. Set your API key: export ANTHROPIC_API_KEY=sk-...
    2. pip install anthropic

Usage (in-process):
    python examples/ai/system_tools_agent_anthropic.py

Usage (server/worker):
    flux start server
    flux start worker
    flux workflow run system_tools_agent_anthropic '{"instruction": "Create a Python script that prints fibonacci numbers"}'
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from flux import ExecutionContext, workflow
from flux.tasks.ai import agent, system_tools


@workflow
async def system_tools_agent_anthropic(ctx: ExecutionContext[dict[str, Any]]):
    """
    Autonomous coding agent powered by Claude.

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
        "You are an autonomous coding assistant. Use your tools to accomplish "
        "the user's request. Always explore before making changes. "
        "Check your work after editing.",
        model="anthropic/claude-sonnet-4-20250514",
        name="coding_assistant",
        tools=tools,
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

    (workspace / "app.py").write_text(
        "from flask import Flask\n\napp = Flask(__name__)\n\n"
        "@app.route('/')\ndef index():\n    return 'Hello World'\n",
    )
    (workspace / "requirements.txt").write_text("flask\n")

    print(f"Workspace: {workspace}")
    print("=" * 80)

    result = system_tools_agent_anthropic.run(
        {
            "instruction": "Review the Flask app, add a /health endpoint and write a basic test file.",
            "workspace": str(workspace),
        },
    )

    if result.has_failed:
        print(f"Failed: {result.output}")
    else:
        print(f"Answer: {result.output['answer']}")
