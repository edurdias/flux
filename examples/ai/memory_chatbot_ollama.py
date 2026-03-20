"""Conversational chatbot with working memory.

The agent remembers the conversation history within the workflow execution.
Working memory stores messages as task events — durable, replay-safe.

Usage:
    flux run examples/ai/memory_chatbot_ollama.py
"""
from __future__ import annotations

from typing import Any

from flux import workflow, ExecutionContext
from flux.tasks.ai import agent
from flux.tasks.ai.memory import working_memory
from flux.tasks import pause

chatbot = agent(
    system_prompt="You are a friendly assistant. Keep responses concise.",
    model="ollama/llama3.2",
    working_memory=working_memory(),
)


@workflow
async def memory_chatbot(ctx: ExecutionContext[dict[str, Any]]):
    initial_input = ctx.input or {}
    message = initial_input.get("message", "Hello!")

    response = await chatbot(message)
    print(f"Assistant: {response}")

    for turn in range(5):
        user_input = await pause(f"turn_{turn}")
        if isinstance(user_input, dict):
            user_input = user_input.get("message", "")
        if not user_input or user_input.lower() == "quit":
            break
        response = await chatbot(str(user_input))
        print(f"Assistant: {response}")

    return "Conversation ended."
