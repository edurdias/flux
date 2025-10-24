"""
Conversational AI Agent using Anthropic Claude.

This example uses Anthropic's Claude API which excels at:
- Long-form conversations with extended context
- Complex reasoning and analysis
- Following detailed instructions

Prerequisites:
    1. Get an Anthropic API key: https://console.anthropic.com/
    2. Set the API key as a secret:
       flux secrets set ANTHROPIC_API_KEY "your-api-key"

Usage:
    # Start a new conversation
    flux workflow run conversational_agent_anthropic '{"message": "Why is the sky blue?"}'

    # Resume the conversation
    flux workflow resume conversational_agent_anthropic <execution_id> '{"message": "Why does the sky turn red and orange during sunset?"}'

    # Specify the model explicitly (uses Claude Sonnet 4.5 by default)
    flux workflow run conversational_agent_anthropic '{"message": "What color would the sky be on Mars?", "model": "claude-sonnet-4-5-20250929"}'
"""

from __future__ import annotations

from typing import Any

from anthropic import AsyncAnthropic

from flux import ExecutionContext, task, workflow
from flux.tasks import pause


@task.with_options(
    secret_requests=["ANTHROPIC_API_KEY"],
    retry_max_attempts=3,
    retry_delay=1,
    retry_backoff=2,
    timeout=60,
)
async def call_anthropic_api(
    messages: list[dict[str, str]],
    system_prompt: str,
    model: str,
    temperature: float,
    max_tokens: int,
    secrets: dict[str, Any] | None = None,
) -> tuple[str, int, int]:
    """
    Call Anthropic API to generate a response using the official SDK.

    Args:
        messages: Conversation history
        system_prompt: System instructions for the model
        model: The Claude model to use
        temperature: Sampling temperature (0.0 to 1.0)
        max_tokens: Maximum tokens in the response
        secrets: Dictionary containing ANTHROPIC_API_KEY

    Returns:
        Tuple of (assistant_response, input_tokens, output_tokens)
    """
    if not secrets or "ANTHROPIC_API_KEY" not in secrets:
        raise ValueError(
            "ANTHROPIC_API_KEY not found. Set it using: "
            "flux secrets set ANTHROPIC_API_KEY 'your-key'",
        )

    try:
        # Create Anthropic client
        client = AsyncAnthropic(api_key=secrets["ANTHROPIC_API_KEY"])

        # Call Anthropic messages API
        response = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=messages,
        )

        assistant_message = response.content[0].text
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens

        return assistant_message, input_tokens, output_tokens

    except Exception as e:
        raise RuntimeError(f"Failed to call Anthropic API: {str(e)}") from e


@task
async def conversation_turn(
    messages: list[dict[str, str]],
    user_message: str,
    system_prompt: str,
    model: str,
    temperature: float,
    max_tokens: int,
) -> tuple[list[dict[str, str]], str, int, int]:
    """
    Process one turn of the conversation.

    Args:
        messages: Current conversation history
        user_message: The user's message
        system_prompt: System prompt for the LLM
        model: Anthropic model to use
        temperature: Sampling temperature
        max_tokens: Maximum tokens per response

    Returns:
        Tuple of (updated messages, assistant response, input tokens, output tokens)
    """
    # Add user message to history
    messages.append({"role": "user", "content": user_message})

    # Call LLM to generate response
    assistant_response, input_tokens, output_tokens = await call_anthropic_api(
        messages=messages,
        system_prompt=system_prompt,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    # Add assistant response to history
    messages.append({"role": "assistant", "content": assistant_response})

    return messages, assistant_response, input_tokens, output_tokens


@workflow.with_options(name="conversational_agent_anthropic")
async def conversational_agent_anthropic(ctx: ExecutionContext[dict[str, Any]]):
    """
    A conversational AI agent using Anthropic's Claude models.

    Initial Input format:
    {
        "message": "User's message",
        "system_prompt": "Optional system prompt",
        "model": "claude-sonnet-4-5-20250929",  # Latest Claude model
        "temperature": 1.0,         # Optional: 0.0 to 1.0
        "max_tokens": 1024,         # Optional: max tokens per response
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
    model = initial_input.get("model", "claude-sonnet-4-5-20250929")
    temperature = initial_input.get("temperature", 1.0)
    max_tokens = initial_input.get("max_tokens", 1024)
    max_turns = initial_input.get("max_turns", 10)

    # Initialize conversation state
    messages: list[dict[str, str]] = []
    total_input_tokens = 0
    total_output_tokens = 0

    # Process first turn
    first_message = initial_input.get("message", "")
    if not first_message:
        return {"error": "No message provided in initial input", "execution_id": ctx.execution_id}

    messages, _, input_tokens, output_tokens = await conversation_turn(
        messages,
        first_message,
        system_prompt,
        model,
        temperature,
        max_tokens,
    )
    total_input_tokens += input_tokens
    total_output_tokens += output_tokens

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
                "total_input_tokens": total_input_tokens,
                "total_output_tokens": total_output_tokens,
                "total_tokens": total_input_tokens + total_output_tokens,
            }

        # Process next turn
        messages, _, input_tokens, output_tokens = await conversation_turn(
            messages,
            next_message,
            system_prompt,
            model,
            temperature,
            max_tokens,
        )
        total_input_tokens += input_tokens
        total_output_tokens += output_tokens

    # Conversation ended - return final result
    return {
        "status": "conversation_ended",
        "reason": f"Maximum turns ({max_turns}) reached",
        "conversation_history": messages,
        "turn_count": len(messages) // 2,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_tokens": total_input_tokens + total_output_tokens,
        "model": model,
        "execution_id": ctx.execution_id,
    }


if __name__ == "__main__":  # pragma: no cover
    import json
    import os
    from flux.secret_managers import SecretManager

    # Check if API key is available
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY not found in environment.")
        print("Set it using: export ANTHROPIC_API_KEY='your-key'")
        exit(1)

    # Set secret for local execution
    secret_manager = SecretManager.current()
    secret_manager.save("ANTHROPIC_API_KEY", api_key)

    # Start conversation about atmospheric physics
    initial_input = {
        "message": "Why is the sky blue?",
        "model": "claude-sonnet-4-5-20250929",
        "max_turns": 3,
    }

    try:
        # Turn 1
        result = conversational_agent_anthropic.run(initial_input)
        if result.has_failed:
            raise Exception(f"Workflow failed: {result.output}")

        # Turn 2
        result = conversational_agent_anthropic.resume(
            result.execution_id,
            {"message": "Why does the sky turn red and orange during sunset?"},
        )
        if result.has_failed:
            raise Exception(f"Workflow failed: {result.output}")

        # Turn 3
        result = conversational_agent_anthropic.resume(
            result.execution_id,
            {"message": "What color would the sky be on Mars?"},
        )
        if result.has_failed:
            raise Exception(f"Workflow failed: {result.output}")

        # Display conversation and token usage
        print(
            f"Total Tokens: {result.output.get('total_tokens', 0)} "
            f"(in: {result.output.get('total_input_tokens', 0)}, "
            f"out: {result.output.get('total_output_tokens', 0)})",
        )
        print(json.dumps(result.output.get("conversation_history", []), indent=2))

    except Exception as e:
        print(f"Error: {e}")
