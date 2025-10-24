"""
Conversational AI Agent using OpenAI (GPT-4/GPT-3.5).

This example uses OpenAI's Chat Completions API for production-grade conversations.

Prerequisites:
    1. Get an OpenAI API key: https://platform.openai.com/api-keys
    2. Set the API key as a secret:
       flux secrets set OPENAI_API_KEY "your-api-key"

Usage:
    # Start a new conversation
    flux workflow run conversational_agent_openai '{"message": "Why is the sky blue?"}'

    # Resume the conversation
    flux workflow resume conversational_agent_openai <execution_id> '{"message": "Why does the sky turn red and orange during sunset?"}'

    # Use GPT-4o mini (faster, cheaper)
    flux workflow run conversational_agent_openai '{"message": "What color would the sky be on Mars?", "model": "gpt-4o-mini"}'
"""

from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI

from flux import ExecutionContext, task, workflow
from flux.tasks import pause


@task.with_options(
    secret_requests=["OPENAI_API_KEY"],
    retry_max_attempts=3,
    retry_delay=1,
    retry_backoff=2,
    timeout=60,
)
async def call_openai_api(
    messages: list[dict[str, str]],
    system_prompt: str,
    model: str,
    temperature: float,
    max_tokens: int,
    secrets: dict[str, Any] | None = None,
) -> tuple[str, int]:
    """Call OpenAI API to generate a response using the official SDK."""
    if not secrets or "OPENAI_API_KEY" not in secrets:
        raise ValueError(
            "OPENAI_API_KEY not found. Set it using: " "flux secrets set OPENAI_API_KEY 'your-key'",
        )

    try:
        client = AsyncOpenAI(api_key=secrets["OPENAI_API_KEY"])

        # Prepare messages with system prompt
        full_messages = [{"role": "system", "content": system_prompt}]
        full_messages.extend(messages)

        # Call OpenAI chat completions API
        response = await client.chat.completions.create(
            model=model,
            messages=full_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        assistant_message = response.choices[0].message.content
        tokens_used = response.usage.total_tokens

        return assistant_message, tokens_used

    except Exception as e:
        raise RuntimeError(f"Failed to call OpenAI API: {str(e)}") from e


@task
async def conversation_turn(
    messages: list[dict[str, str]],
    user_message: str,
    system_prompt: str,
    model: str,
    temperature: float,
    max_tokens: int,
) -> tuple[list[dict[str, str]], str, int]:
    """
    Process one turn of the conversation.

    Args:
        messages: Current conversation history
        user_message: The user's message
        system_prompt: System prompt for the LLM
        model: OpenAI model to use
        temperature: Sampling temperature
        max_tokens: Maximum tokens per response

    Returns:
        Tuple of (updated messages, assistant response, tokens used)
    """
    # Add user message to history
    messages.append({"role": "user", "content": user_message})

    # Call LLM to generate response
    assistant_response, tokens_used = await call_openai_api(
        messages=messages,
        system_prompt=system_prompt,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    # Add assistant response to history
    messages.append({"role": "assistant", "content": assistant_response})

    return messages, assistant_response, tokens_used


@workflow.with_options(name="conversational_agent_openai")
async def conversational_agent_openai(ctx: ExecutionContext[dict[str, Any]]):
    """
    A conversational AI agent using OpenAI's GPT models.

    Initial Input format:
    {
        "message": "User's message",
        "system_prompt": "Optional system prompt",
        "model": "gpt-4o",          # or "gpt-4o-mini", "gpt-4-turbo", etc.
        "temperature": 0.7,         # Optional: 0.0 to 2.0
        "max_tokens": 500,          # Optional: max tokens per response
        "max_turns": 10             # Optional: maximum conversation turns
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
    model = initial_input.get("model", "gpt-4o")
    temperature = initial_input.get("temperature", 0.7)
    max_tokens = initial_input.get("max_tokens", 500)
    max_turns = initial_input.get("max_turns", 10)

    # Initialize conversation state
    messages: list[dict[str, str]] = []
    total_tokens = 0

    # Process first turn
    first_message = initial_input.get("message", "")
    if not first_message:
        return {"error": "No message provided in initial input", "execution_id": ctx.execution_id}

    messages, _, tokens_used = await conversation_turn(
        messages,
        first_message,
        system_prompt,
        model,
        temperature,
        max_tokens,
    )
    total_tokens += tokens_used

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
                "total_tokens": total_tokens,
            }

        # Process next turn
        messages, _, tokens_used = await conversation_turn(
            messages,
            next_message,
            system_prompt,
            model,
            temperature,
            max_tokens,
        )
        total_tokens += tokens_used

    # Conversation ended - return final result
    return {
        "status": "conversation_ended",
        "reason": f"Maximum turns ({max_turns}) reached",
        "conversation_history": messages,
        "turn_count": len(messages) // 2,
        "total_tokens": total_tokens,
        "model": model,
        "execution_id": ctx.execution_id,
    }


if __name__ == "__main__":  # pragma: no cover
    import json
    import os
    from flux.secret_managers import SecretManager

    # Check if API key is available
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("Error: OPENAI_API_KEY not found in environment.")
        print("Set it using: export OPENAI_API_KEY='your-key'")
        exit(1)

    # Set secret for local execution
    secret_manager = SecretManager.current()
    secret_manager.save("OPENAI_API_KEY", api_key)

    # Start conversation about atmospheric physics
    initial_input = {"message": "Why is the sky blue?", "model": "gpt-4o", "max_turns": 3}

    try:
        # Turn 1
        result = conversational_agent_openai.run(initial_input)
        if result.has_failed:
            raise Exception(f"Workflow failed: {result.output}")

        # Turn 2
        result = conversational_agent_openai.resume(
            result.execution_id,
            {"message": "Why does the sky turn red and orange during sunset?"},
        )
        if result.has_failed:
            raise Exception(f"Workflow failed: {result.output}")

        # Turn 3
        result = conversational_agent_openai.resume(
            result.execution_id,
            {"message": "What color would the sky be on Mars?"},
        )
        if result.has_failed:
            raise Exception(f"Workflow failed: {result.output}")

        # Display conversation and token usage
        print(f"Total Tokens: {result.output.get('total_tokens', 0)}")
        print(json.dumps(result.output.get("conversation_history", []), indent=2))

    except Exception as e:
        print(f"Error: {e}")
