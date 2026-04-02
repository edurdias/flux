"""
Tool Approval Example.

Demonstrates human-in-the-loop approval for dangerous tool calls.
The agent has system tools with the shell command requiring approval.

Prerequisites:
    1. Install Ollama and pull a model: ollama pull llama3.2
    2. Start Ollama service: ollama serve

Usage:
    flux workflow register examples/ai/tool_approval_agent.py
    flux workflow run tool_approval_demo '{"task": "List files in the current directory"}'

    When the agent calls the shell tool, the workflow will pause.
    Check the execution status to see the pending approval:
        flux workflow status tool_approval_demo <execution_id>

    Approve the tool call:
        flux workflow resume tool_approval_demo <execution_id> '{"approved": true}'

    Or reject:
        flux workflow resume tool_approval_demo <execution_id> '{"approved": false}'

    Or approve and skip future approvals for this tool:
        flux workflow resume tool_approval_demo <execution_id> '{"approved": true, "always_approve": true}'
"""

from __future__ import annotations

from typing import Any

from flux import ExecutionContext, workflow
from flux.tasks.ai import agent
from flux.tasks.ai.approval import requires_approval
from flux.tasks.ai.tools.system_tools import system_tools


@workflow
async def tool_approval_demo(ctx: ExecutionContext[dict[str, Any]]):
    raw = ctx.input or {}
    task_description = raw.get("task", "List the files in the current directory")

    tools = requires_approval(
        system_tools("./workspace", timeout=10),
        only=["shell"],
    )

    assistant = await agent(
        "You are a helpful assistant with access to system tools. "
        "Use them to complete the user's request.",
        model="ollama/llama3.2",
        name="tool_approval_assistant",
        tools=tools,
        stream=False,
    )

    return await assistant(task_description)


if __name__ == "__main__":  # pragma: no cover
    import json

    result = tool_approval_demo.run({"task": "List all Python files"})
    if result.is_paused:
        print(f"Paused for approval. Execution ID: {result.execution_id}")
        print(f"Pending: {json.dumps(result.output, indent=2)}")
    elif result.has_succeeded:
        print(result.output)
    else:
        print(f"Failed: {result.output}")
