"""
Tool Approval Example.

Demonstrates human-in-the-loop approval for dangerous tool calls. The agent
has the standard system tool set, with the shell tool gated by the engine's
``requires_approval`` primitive — when the model calls ``shell``, the
workflow pauses until an operator approves or rejects the call.

Prerequisites:
    1. Install Ollama and pull a model: ollama pull llama3.2
    2. Start Ollama service: ollama serve

Usage:
    flux workflow register examples/ai/tool_approval_agent.py
    flux workflow run tool_approval_demo '{"task": "List files in the current directory"}'

    When the agent calls the shell tool the workflow pauses; inspect with:
        flux execution approvals --execution <execution_id>

    Approve:
        flux execution approve <execution_id> <task_call_id> --reason "lgtm"

    Reject:
        flux execution reject  <execution_id> <task_call_id> --reason "no"
"""

from __future__ import annotations

from typing import Any

from flux import ExecutionContext, workflow
from flux.tasks.ai import agent
from flux.tasks.ai.tools.system_tools import system_tools


def _gate_shell(tools: list) -> list:
    """Wrap only the ``shell`` tool with requires_approval=True; pass others through."""
    gated: list = []
    for tool in tools:
        func = tool.func if hasattr(tool, "func") else tool
        if getattr(func, "__name__", "") == "shell" and hasattr(tool, "with_options"):
            gated.append(tool.with_options(requires_approval=True))
        else:
            gated.append(tool)
    return gated


@workflow
async def tool_approval_demo(ctx: ExecutionContext[dict[str, Any]]):
    raw = ctx.input or {}
    task_description = raw.get("task", "List the files in the current directory")

    tools = _gate_shell(system_tools("./workspace", timeout=10))

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
