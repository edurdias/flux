"""
Conversational AI Agent using LangChain + Ollama (Local LLM).

This example demonstrates using LangChain for LLM interaction while Flux provides
workflow orchestration (retries, pause/resume, durability).

Compared to examples/ai/conversational_agent_ollama.py (pure Flux + Ollama SDK),
this variant delegates LLM calls to LangChain's ChatOllama, showing how to integrate
LangChain's ecosystem (prompt templates, chains, memory abstractions) with Flux.

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a model: ollama pull llama3
    3. Start Ollama service: ollama serve
    4. Install dependencies: pip install langchain-core langchain-ollama

Usage:
    # Start a new conversation
    flux workflow run conversational_agent_langchain '{"message": "Why is the sky blue?"}'

    # Resume the conversation
    flux workflow resume conversational_agent_langchain <execution_id> '{"message": "Why does the sky turn red and orange during sunset?"}'

    # Use a different model
    flux workflow run conversational_agent_langchain '{"message": "What color would the sky be on Mars?", "model": "qwen2.5:0.5b"}'
"""

from __future__ import annotations

from typing import Any

from langchain_core.chat_history import InMemoryChatMessageHistory
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

from flux import ExecutionContext, task, workflow
from flux.tasks import pause


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=60)
async def call_langchain_chat(
    history: list[dict[str, str]],
    user_message: str,
    system_prompt: str,
    model: str,
    ollama_url: str,
) -> tuple[list[dict[str, str]], str]:
    """Process one conversation turn via LangChain ChatOllama."""
    try:
        chat_history = InMemoryChatMessageHistory()
        for msg in history:
            if msg["role"] == "user":
                chat_history.add_user_message(msg["content"])
            elif msg["role"] == "assistant":
                chat_history.add_ai_message(msg["content"])

        chat_history.add_user_message(user_message)

        langchain_messages: list[Any] = [
            SystemMessage(content=system_prompt),
            *chat_history.messages,
        ]

        llm = ChatOllama(model=model, base_url=ollama_url)
        response = await llm.ainvoke(langchain_messages)

        chat_history.add_ai_message(response.content)

        serialized = [
            {"role": "user" if isinstance(m, HumanMessage) else "assistant", "content": m.content}
            for m in chat_history.messages
        ]
        return serialized, response.content

    except Exception as e:
        raise RuntimeError(
            f"Failed to call LangChain ChatOllama: {str(e)}. "
            "Make sure Ollama is running (ollama serve) and the model is available.",
        ) from e


@workflow
async def conversational_agent_langchain(ctx: ExecutionContext[dict[str, Any]]):
    """
    A conversational AI agent using LangChain + ChatOllama for local LLM inference.

    Initial Input format:
    {
        "message": "User's message",
        "system_prompt": "Optional system prompt",
        "model": "llama3",          # or other Ollama models
        "max_turns": 10,            # Optional: maximum conversation turns
        "ollama_url": "http://localhost:11434"  # Optional: Ollama server URL
    }

    Resume Input format:
    {
        "message": "User's next message"
    }
    """
    initial_input = ctx.input or {}
    system_prompt = initial_input.get(
        "system_prompt",
        "You are a helpful AI assistant. Be concise and informative.",
    )
    model = initial_input.get("model", "llama3")
    max_turns = initial_input.get("max_turns", 10)
    ollama_url = initial_input.get("ollama_url", "http://localhost:11434")

    messages: list[dict[str, str]] = []

    first_message = initial_input.get("message", "")
    if not first_message:
        return {"error": "No message provided in initial input", "execution_id": ctx.execution_id}

    messages, _ = await call_langchain_chat(
        messages,
        first_message,
        system_prompt,
        model,
        ollama_url,
    )

    for turn in range(1, max_turns):
        resume_input = await pause(f"waiting_for_user_input_turn_{turn}")

        next_message = resume_input.get("message", "") if resume_input else ""
        if not next_message:
            return {
                "error": "No message provided in resume input",
                "turn_count": len(messages) // 2,
                "execution_id": ctx.execution_id,
                "conversation_history": messages,
            }

        messages, _ = await call_langchain_chat(
            messages,
            next_message,
            system_prompt,
            model,
            ollama_url,
        )

    return {
        "status": "conversation_ended",
        "reason": f"Maximum turns ({max_turns}) reached",
        "conversation_history": messages,
        "turn_count": len(messages) // 2,
        "model": model,
        "execution_id": ctx.execution_id,
    }


if __name__ == "__main__":  # pragma: no cover
    import json

    initial_input = {"message": "Why is the sky blue?", "model": "llama3", "max_turns": 3}

    try:
        result = conversational_agent_langchain.run(initial_input)
        if result.has_failed:
            raise Exception(f"Workflow failed: {result.output}")

        result = conversational_agent_langchain.resume(
            result.execution_id,
            {"message": "Why does the sky turn red and orange during sunset?"},
        )
        if result.has_failed:
            raise Exception(f"Workflow failed: {result.output}")

        result = conversational_agent_langchain.resume(
            result.execution_id,
            {"message": "What color would the sky be on Mars?"},
        )
        if result.has_failed:
            raise Exception(f"Workflow failed: {result.output}")

        print(json.dumps(result.output.get("conversation_history", []), indent=2))

    except Exception as e:
        print(f"Error: {e}")
        print("Make sure Ollama is running: ollama serve")
        print("And that you have pulled a model: ollama pull llama3")
