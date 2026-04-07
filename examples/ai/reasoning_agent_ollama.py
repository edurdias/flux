"""
Reasoning Agent with Chain-of-Thought using Ollama.

Demonstrates an agent that uses reasoning/thinking models to show its
chain of thought while calling tools. The thinking traces are stored
in working memory and visible in execution events.

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a reasoning model: ollama pull qwen3
    3. Start Ollama service: ollama serve

Usage (in-process):
    python examples/ai/reasoning_agent_ollama.py

Usage (server/worker):
    flux start server
    flux start worker
    flux workflow register examples/ai/reasoning_agent_ollama.py
    flux workflow run reasoning_agent '{"message": "What files are in the workspace?", "reasoning_effort": "high"}'
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from flux import ExecutionContext, workflow
from flux.config import Configuration
from flux.tasks.ai import agent, system_tools
from flux.tasks.ai.memory import working_memory


@workflow
async def reasoning_agent(ctx: ExecutionContext[dict[str, Any]]):
    """Agent with reasoning/thinking enabled."""
    input_data = ctx.input or {}
    message = input_data.get("message", "List all files in the workspace")
    model = input_data.get("model", "ollama/qwen3")
    effort = input_data.get("reasoning_effort", "high")

    flux_home = Path(Configuration.get().settings.home)
    workspace = flux_home / "reasoning"
    workspace.mkdir(parents=True, exist_ok=True)

    wm = working_memory(max_tokens=50_000)
    tools = system_tools(workspace=str(workspace), timeout=30)

    assistant = await agent(
        "You are a helpful assistant. Think carefully before acting. "
        "Use your tools to accomplish tasks.",
        model=model,
        name="reasoning_agent",
        tools=tools,
        working_memory=wm,
        reasoning_effort=effort,
        max_tool_calls=10,
        stream=False,
    )

    answer = await assistant(message)

    wm_messages = wm.recall()
    thinking_messages = [m for m in wm_messages if m["role"] == "thinking"]

    return {
        "answer": answer,
        "thinking_count": len(thinking_messages),
        "thinking_traces": [m["content"] for m in thinking_messages],
        "working_memory_roles": [m["role"] for m in wm_messages],
    }


if __name__ == "__main__":
    import json

    flux_home = Path(Configuration.get().settings.home)
    workspace = flux_home / "reasoning"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "hello.py").write_text('def greet(name):\n    return f"Hello, {name}!"\n')
    (workspace / "config.yaml").write_text("database: postgres\nport: 5432\n")

    print(f"Workspace: {workspace}")
    print("=" * 70)

    result = reasoning_agent.run(
        {
            "message": "List all files and read hello.py. What does the greet function do?",
            "reasoning_effort": "high",
        },
    )

    if result.has_succeeded:
        output = result.output
        print(f"\nAnswer: {output['answer'][:300]}")
        print(f"\nThinking traces ({output['thinking_count']}):")
        for i, trace in enumerate(output["thinking_traces"]):
            data = json.loads(trace)
            text = data.get("text", "")
            print(f"  {i + 1}. {text[:150]}...")
        print(f"\nWM roles: {output['working_memory_roles']}")
    elif result.has_failed:
        print(f"Failed: {result.output}")
