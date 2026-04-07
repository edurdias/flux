"""
Agent with Working Memory, Long-Term Memory, and Dreaming.

Demonstrates a coding assistant that:
- Stores full tool interactions in working memory (tool_call + tool_result)
- Uses long-term memory for persistent facts across sessions
- Fires a dream workflow on completion for memory consolidation

The dream workflow runs four phases (orient, gather signal, consolidate, prune)
to distill working memory into clean long-term knowledge.

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a model: ollama pull llama3.2
    3. Start Ollama service: ollama serve

Usage (in-process):
    python examples/ai/dreaming_agent_ollama.py

Usage (server/worker):
    flux start server
    flux start worker
    flux workflow register examples/ai/dreaming_agent_ollama.py
    flux workflow register flux/tasks/ai/dreaming.py
    flux workflow run dreaming_agent '{"task": "Explore the workspace and list all files"}'
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from flux import ExecutionContext, workflow
from flux.tasks.ai import agent, system_tools
from flux.tasks.ai.dreaming import dream
from flux.tasks.ai.memory import long_term_memory, sqlite, working_memory


@workflow
async def dreaming_agent(ctx: ExecutionContext[dict[str, Any]]):
    """
    Coding assistant with working memory, long-term memory, and dreaming.

    Input format:
    {
        "task": "What should the agent do?",
        "workspace": "/optional/path/to/workspace"
    }
    """
    input_data = ctx.input or {}
    task_description = input_data.get("task", "List all files in the workspace")

    workspace = Path(input_data.get("workspace", tempfile.mkdtemp(prefix="flux_dreaming_")))
    if not workspace.exists():
        workspace.mkdir(parents=True)

    wm = working_memory(max_tokens=50_000)
    ltm = long_term_memory(
        provider=sqlite(str(workspace / "memory.db")),
        agent="dreaming_agent",
        scope="default",
    )

    tools = system_tools(workspace=str(workspace), timeout=30)

    assistant = await agent(
        "You are a helpful coding assistant. Use your tools to accomplish tasks. "
        "Be concise in your responses.",
        model="ollama/llama3.2",
        name="dreaming_agent",
        tools=tools,
        working_memory=wm,
        long_term_memory=ltm,
        max_tool_calls=10,
        stream=False,
        on_complete=[dream(working_memory=wm, long_term_memory=ltm)],
    )

    answer = await assistant(task_description)

    wm_messages = wm.recall()

    return {
        "task": task_description,
        "answer": answer,
        "workspace": str(workspace),
        "working_memory_count": len(wm_messages),
        "working_memory_roles": [m["role"] for m in wm_messages],
        "execution_id": ctx.execution_id,
    }


if __name__ == "__main__":
    workspace = Path(tempfile.mkdtemp(prefix="flux_dreaming_"))
    (workspace / "hello.py").write_text('def greet(name):\n    return f"Hello, {name}!"\n')
    (workspace / "config.yaml").write_text("database: postgres\nport: 5432\n")
    (workspace / "README.md").write_text("# Sample Project\n\nA demo for dreaming agents.\n")

    print(f"Workspace: {workspace}")
    print("=" * 70)

    result = dreaming_agent.run(
        {
            "task": "List all files and show the contents of hello.py",
            "workspace": str(workspace),
        },
    )

    if result.has_succeeded:
        output = result.output
        print(f"\nAnswer:\n{output['answer'][:500]}")
        print(f"\nWorking Memory: {output['working_memory_count']} messages")
        print(f"Roles: {output['working_memory_roles']}")
    elif result.has_failed:
        print(f"\nFailed: {result.output}")
    else:
        print(f"\nState: {result.state}")
