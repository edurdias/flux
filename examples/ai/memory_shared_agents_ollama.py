"""Two agents sharing long-term memory.

The reviewer stores findings, the summarizer reads them.

Usage:
    flux run examples/ai/memory_shared_agents_ollama.py
"""
from __future__ import annotations

from typing import Any

from flux import workflow, ExecutionContext
from flux.tasks.ai import agent
from flux.tasks.ai.memory import long_term_memory, in_memory

shared = long_term_memory(provider=in_memory(), scope="review:pr-42")


@workflow
async def memory_shared_agents(ctx: ExecutionContext[dict[str, Any]]):
    initial_input = ctx.input or {}
    if isinstance(initial_input, str):
        import json

        initial_input = json.loads(initial_input)
    code = initial_input.get("code", "def add(a, b): return a + b  # TODO: add validation")

    reviewer = await agent(
        system_prompt=(
            "You are a code reviewer. Analyze the code and store your findings "
            "using store_memory. Organize findings by category (bugs, style, security)."
        ),
        model="ollama/llama3.2",
        long_term_memory=shared,
    )

    summarizer = await agent(
        system_prompt=(
            "You are a summary writer. Use recall_memory and list_memory_keys to read "
            "the reviewer's findings, then write a concise summary."
        ),
        model="ollama/llama3.2",
        long_term_memory=shared,
    )

    await reviewer(f"Review this code:\n\n```python\n{code}\n```")
    summary = await summarizer("Summarize the code review findings.")
    return summary
