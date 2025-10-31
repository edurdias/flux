"""
Conversational AI Agent using Ollama (Local LLM).

This example uses Ollama for running local LLMs, which is great for:
- Development and testing without API costs
- Privacy-sensitive applications
- Offline environments
- Custom fine-tuned models

Prerequisites:
    1. Install Ollama: https://ollama.ai
    2. Pull a model: ollama pull llama3
    3. Start Ollama service: ollama serve

Usage:
    # Start a new conversation
    flux workflow run conversational_agent_ollama '{"message": "Why is the sky blue?"}'

    # Resume the conversation
    flux workflow resume conversational_agent_ollama <execution_id> '{"message": "Why does the sky turn red and orange during sunset?"}'

    # Use a different model
    flux workflow run conversational_agent_ollama '{"message": "What color would the sky be on Mars?", "model": "qwen2.5:0.5b"}'
"""

from __future__ import annotations

from typing import Any

from ollama import AsyncClient

from flux import ExecutionContext, task, workflow
from flux.tasks import pause


@task.with_options(retry_max_attempts=3, retry_delay=1, retry_backoff=2, timeout=60)
async def call_ollama_api(
    messages: list[dict[str, str]],
    system_prompt: str,
    model: str,
    ollama_url: str,
) -> str:
    """Call Ollama API to generate a response using the official SDK."""
    try:
        client = AsyncClient(host=ollama_url)

        # Prepare messages with system prompt
        full_messages = [{"role": "system", "content": system_prompt}]
        full_messages.extend(messages)

        # Call Ollama chat API
        response = await client.chat(model=model, messages=full_messages)

        return response["message"]["content"]

    except Exception as e:
        raise RuntimeError(
            f"Failed to call Ollama API: {str(e)}. "
            "Make sure Ollama is running (ollama serve) and the model is available.",
        ) from e


@task
async def conversation_turn(
    messages: list[dict[str, str]],
    user_message: str,
    system_prompt: str,
    model: str,
    ollama_url: str,
) -> tuple[list[dict[str, str]], str]:
    """
    Process one turn of the conversation.

    Args:
        messages: Current conversation history
        user_message: The user's message
        system_prompt: System prompt for the LLM
        model: Ollama model to use
        ollama_url: Ollama server URL

    Returns:
        Tuple of (updated messages, assistant response)
    """
    # Add user message to history
    messages.append({"role": "user", "content": user_message})

    # Call LLM to generate response
    assistant_response = await call_ollama_api(
        messages=messages,
        system_prompt=system_prompt,
        model=model,
        ollama_url=ollama_url,
    )

    # Add assistant response to history
    messages.append({"role": "assistant", "content": assistant_response})

    return messages, assistant_response


@workflow.with_options(name="conversational_agent_ollama")
async def conversational_agent_ollama(ctx: ExecutionContext[dict[str, Any]]):
    """
    A conversational AI agent using Ollama for local LLM inference.

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
    # Get initial configuration from input
    initial_input = ctx.input or {}
    system_prompt = initial_input.get(
        "system_prompt",
        "You are a helpful AI assistant. Be concise and informative.",
    )
    model = initial_input.get("model", "llama3")
    max_turns = initial_input.get("max_turns", 10)
    ollama_url = initial_input.get("ollama_url", "http://localhost:11434")

    # Initialize conversation state
    messages: list[dict[str, str]] = []

    # Process first turn
    first_message = initial_input.get("message", "")
    if not first_message:
        return {"error": "No message provided in initial input", "execution_id": ctx.execution_id}

    messages, _ = await conversation_turn(messages, first_message, system_prompt, model, ollama_url)

    # Main conversation loop - pause between turns
    for turn in range(1, max_turns):
        # Pause and wait for next user input
        # When resumed, pause returns the input provided during resume
        # Use a unique label for each pause to allow Flux to differentiate between them
        resume_input = await pause(f"waiting_for_user_input_turn_{turn}")

        # Get next message from resume input
        next_message = resume_input.get("message", "") if resume_input else ""
        if not next_message:
            return {
                "error": "No message provided in resume input",
                "turn_count": len(messages) // 2,
                "execution_id": ctx.execution_id,
                "conversation_history": messages,
            }

        # Process next turn
        messages, _ = await conversation_turn(
            messages,
            next_message,
            system_prompt,
            model,
            ollama_url,
        )

    # Conversation ended - return final result
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

    # Start conversation about atmospheric physics
    initial_input = {"message": "Why is the sky blue?", "model": "llama3", "max_turns": 3}

    try:
        # Turn 1
        result = conversational_agent_ollama.run(initial_input)
        if result.has_failed:
            raise Exception(f"Workflow failed: {result.output}")

        # Turn 2
        result = conversational_agent_ollama.resume(
            result.execution_id,
            {"message": "Why does the sky turn red and orange during sunset?"},
        )
        if result.has_failed:
            raise Exception(f"Workflow failed: {result.output}")

        # Turn 3
        result = conversational_agent_ollama.resume(
            result.execution_id,
            {"message": "What color would the sky be on Mars?"},
        )
        if result.has_failed:
            raise Exception(f"Workflow failed: {result.output}")

        # Display conversation
        print(json.dumps(result.output.get("conversation_history", []), indent=2))

    except Exception as e:
        print(f"Error: {e}")
        print("Make sure Ollama is running: ollama serve")
        print("And that you have pulled a model: ollama pull llama3")
