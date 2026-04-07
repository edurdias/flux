"""
Agent with Working Memory, Long-Term Memory, and Dreaming.

Demonstrates a coding assistant that:
- Stores full tool interactions in working memory (tool_call + tool_result)
- Uses long-term memory for persistent facts across sessions
- Supports multi-turn conversations via pause/resume
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
    flux workflow run dreaming_agent '{"message": "Explore the workspace and list all files"}'

    # Follow up (resume the same execution):
    flux workflow resume dreaming_agent <execution_id> '{"message": "What database is configured?"}'

    # End the conversation:
    flux workflow resume dreaming_agent <execution_id> '{"message": ""}'
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from flux import ExecutionContext, workflow
from flux.tasks import pause
from flux.tasks.ai import agent, system_tools
from flux.tasks.ai.dreaming import dream
from flux.tasks.ai.memory import long_term_memory, sqlite, working_memory


@workflow
async def dreaming_agent(ctx: ExecutionContext[dict[str, Any]]):
    """
    Multi-turn coding assistant with working memory, long-term memory, and dreaming.

    Initial input:
    {
        "message": "What should the agent do?",
        "workspace": "/optional/path/to/workspace",
        "max_turns": 10
    }

    Resume input:
    {
        "message": "Follow-up question or instruction"
    }
    """
    from flux.config import Configuration

    input_data = ctx.input or {}
    first_message = input_data.get("message", "List all files in the workspace")

    flux_home = Path(Configuration.get().settings.home)
    default_workspace = str(flux_home / "dreaming")
    workspace = Path(input_data.get("workspace", default_workspace))
    workspace.mkdir(parents=True, exist_ok=True)

    max_turns = input_data.get("max_turns", 10)

    wm = working_memory(max_tokens=50_000)
    ltm = long_term_memory(
        provider=sqlite(str(workspace / "memory.db")),
        agent="dreaming_agent",
        scope="default",
    )

    tools = system_tools(workspace=str(workspace), timeout=30)

    assistant = await agent(
        "You are a helpful coding assistant. Use your tools to accomplish tasks. "
        "Always check your long-term memory first for relevant context. "
        "Store important facts you learn using store_memory. "
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

    message = first_message
    conversation = []

    for turn in range(max_turns):
        answer = await assistant(message)
        conversation.append({"turn": turn + 1, "user": message, "assistant": answer})

        resume_input = await pause(f"waiting_for_input_turn_{turn + 1}")

        next_message = (resume_input or {}).get("message", "")
        if not next_message:
            break

        message = next_message

    wm_messages = wm.recall()
    ltm_keys = await ltm.keys()

    return {
        "workspace": str(workspace),
        "conversation": conversation,
        "working_memory_count": len(wm_messages),
        "working_memory_roles": [m["role"] for m in wm_messages],
        "ltm_keys": ltm_keys,
        "execution_id": ctx.execution_id,
    }


def _print_result(result):
    if result.has_succeeded:
        output = result.output
        print(f"\nConversation ({len(output['conversation'])} turns):")
        for turn in output["conversation"]:
            print(f"  Turn {turn['turn']}:")
            print(f"    User: {turn['user'][:100]}")
            print(f"    Agent: {turn['assistant'][:200]}")
        print(f"\nWorking Memory: {output['working_memory_count']} messages")
        print(f"LTM keys: {output['ltm_keys']}")
    elif result.has_failed:
        print(f"\nFailed: {result.output}")
    elif result.is_paused:
        print(f"\nPaused. Execution ID: {result.execution_id}")


if __name__ == "__main__":
    # Use Flux home for persistent data (defaults to .flux/)
    from flux.config import Configuration

    flux_home = Path(Configuration.get().settings.home)
    workspace = flux_home / "dreaming"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "hello.py").write_text('def greet(name):\n    return f"Hello, {name}!"\n')
    (workspace / "config.yaml").write_text(
        "database: postgres\nport: 5432\nhost: db.example.com\n",
    )
    (workspace / "main.py").write_text(
        'from fastapi import FastAPI\napp = FastAPI()\n\n@app.get("/")\ndef root():\n    return {"status": "ok"}\n',
    )
    (workspace / "README.md").write_text(
        "# My Project\n\nA Python web API using FastAPI and PostgreSQL.\n",
    )

    print(f"Workspace: {workspace}")
    print(f"Memory DB: {workspace / 'memory.db'}")
    print("=" * 70)

    # --- Session 1: Explore and learn ---
    print("\n=== SESSION 1: Explore and learn ===")
    result = dreaming_agent.run(
        {
            "message": "Explore the workspace. List all files and read config.yaml. "
            "Store the database host, port, and type in your long-term memory.",
            "workspace": str(workspace),
        },
    )

    if result.is_paused:
        # End session 1
        result = dreaming_agent.resume(result.execution_id, {"message": ""})

    _print_result(result)

    # --- Session 2: New execution — recall from LTM ---
    print("\n=== SESSION 2: New execution — what do you remember? ===")
    result = dreaming_agent.run(
        {
            "message": "Without using any tools, what database configuration do you "
            "remember from previous sessions? Check your long-term memory.",
            "workspace": str(workspace),
        },
    )

    if result.is_paused:
        result = dreaming_agent.resume(result.execution_id, {"message": ""})

    _print_result(result)
