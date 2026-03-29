"""Agent with long-term memory using SQLite.

The agent remembers facts across workflow executions.
Long-term memory is exposed as tools — the LLM decides what to store and recall.

Usage:
    flux run examples/ai/memory_long_term_ollama.py
"""
from __future__ import annotations

from typing import Any

from flux import workflow, ExecutionContext
from flux.tasks.ai import agent
from flux.tasks.ai.memory import working_memory, long_term_memory, sqlite


@workflow
async def memory_long_term(ctx: ExecutionContext[dict[str, Any]]):
    initial_input = ctx.input or {}
    if isinstance(initial_input, str):
        import json

        initial_input = json.loads(initial_input)
    message = initial_input.get(
        "message",
        "Hi! My name is Alice and I work as a software developer.",
    )

    assistant = await agent(
        system_prompt=(
            "You are a personal assistant. Remember important facts about the user "
            "using your memory tools. Always check memory at the start of a conversation."
        ),
        model="ollama/llama3.2",
        working_memory=working_memory(),
        long_term_memory=long_term_memory(
            provider=sqlite("memory_example.db"),
            scope="user:default",
        ),
    )
    response = await assistant(message)
    return response
