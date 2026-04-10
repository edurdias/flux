"""
Conversational AI Agent using Google Gemini.

This example uses Google's Gemini API which excels at:
- Long-form conversations with extended context
- Complex reasoning and analysis
- Following detailed instructions

Prerequisites:
    1. Get a Gemini API key: https://aistudio.google.com/apikey
    2. Set the API key as a secret:
       flux secrets set GEMINI_API_KEY "your-api-key"

Usage:
    # Start a new conversation
    flux workflow run conversational_agent_gemini '{"message": "Why is the sky blue?"}'

    # Resume the conversation
    flux workflow resume conversational_agent_gemini <execution_id> '{"message": "Why does the sky turn red and orange during sunset?"}'

    # Specify the model explicitly (uses Gemini 2.5 Flash by default)
    flux workflow run conversational_agent_gemini '{"message": "What color would the sky be on Mars?", "model": "gemini-2.5-pro"}'
"""

from __future__ import annotations

from typing import Any

from google import genai
from google.genai import types

from flux import ExecutionContext, task, workflow
from flux.tasks import pause


@task.with_options(
    secret_requests=["GEMINI_API_KEY"],
    retry_max_attempts=3,
    retry_delay=1,
    retry_backoff=2,
    timeout=60,
)
async def call_gemini_api(
    messages: list[dict[str, str]],
    system_prompt: str,
    model: str,
    max_tokens: int,
    secrets: dict[str, Any] | None = None,
) -> tuple[str, int, int]:
    """
    Call Gemini API to generate a response using the official SDK.

    Args:
        messages: Conversation history
        system_prompt: System instructions for the model
        model: The Gemini model to use
        max_tokens: Maximum tokens in the response
        secrets: Dictionary containing GEMINI_API_KEY

    Returns:
        Tuple of (assistant_response, input_tokens, output_tokens)
    """
    if not secrets or "GEMINI_API_KEY" not in secrets:
        raise ValueError(
            "GEMINI_API_KEY not found. Set it using: flux secrets set GEMINI_API_KEY 'your-key'",
        )

    try:
        client = genai.Client(api_key=secrets["GEMINI_API_KEY"])

        contents = [
            types.Content(
                role="model" if msg["role"] == "assistant" else "user",
                parts=[types.Part(text=msg["content"])],
            )
            for msg in messages
        ]

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=max_tokens,
        )

        response = await client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )

        assistant_message = response.text
        input_tokens = response.usage_metadata.prompt_token_count
        output_tokens = response.usage_metadata.candidates_token_count

        return assistant_message, input_tokens, output_tokens

    except Exception as e:
        raise RuntimeError(f"Failed to call Gemini API: {str(e)}") from e


@task
async def conversation_turn(
    messages: list[dict[str, str]],
    user_message: str,
    system_prompt: str,
    model: str,
    max_tokens: int,
) -> tuple[list[dict[str, str]], str, int, int]:
    """
    Process one turn of the conversation.

    Args:
        messages: Current conversation history
        user_message: The user's message
        system_prompt: System prompt for the LLM
        model: Gemini model to use
        max_tokens: Maximum tokens per response

    Returns:
        Tuple of (updated messages, assistant response, input tokens, output tokens)
    """
    messages.append({"role": "user", "content": user_message})

    assistant_response, input_tokens, output_tokens = await call_gemini_api(
        messages=messages,
        system_prompt=system_prompt,
        model=model,
        max_tokens=max_tokens,
    )

    messages.append({"role": "assistant", "content": assistant_response})

    return messages, assistant_response, input_tokens, output_tokens


@workflow
async def conversational_agent_gemini(ctx: ExecutionContext[dict[str, Any]]):
    """
    A conversational AI agent using Google's Gemini models.

    Initial Input format:
    {
        "message": "User's message",
        "system_prompt": "Optional system prompt",
        "model": "gemini-2.5-flash",  # Latest Gemini model
        "max_tokens": 1024,           # Optional: max tokens per response
        "max_turns": 10               # Optional: maximum conversation turns
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
    model = initial_input.get("model", "gemini-2.5-flash")
    max_tokens = initial_input.get("max_tokens", 1024)
    max_turns = initial_input.get("max_turns", 10)

    messages: list[dict[str, str]] = []
    total_input_tokens = 0
    total_output_tokens = 0

    first_message = initial_input.get("message", "")
    if not first_message:
        return {"error": "No message provided in initial input", "execution_id": ctx.execution_id}

    messages, _, input_tokens, output_tokens = await conversation_turn(
        messages,
        first_message,
        system_prompt,
        model,
        max_tokens,
    )
    total_input_tokens += input_tokens
    total_output_tokens += output_tokens

    for turn in range(1, max_turns):
        resume_input = await pause(f"waiting_for_user_input_turn_{turn}")

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

        messages, _, input_tokens, output_tokens = await conversation_turn(
            messages,
            next_message,
            system_prompt,
            model,
            max_tokens,
        )
        total_input_tokens += input_tokens
        total_output_tokens += output_tokens

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

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY not found in environment.")
        print("Set it using: export GEMINI_API_KEY='your-key'")
        exit(1)

    secret_manager = SecretManager.current()
    secret_manager.save("GEMINI_API_KEY", api_key)

    initial_input = {
        "message": "Why is the sky blue?",
        "model": "gemini-2.5-flash",
        "max_turns": 3,
    }

    try:
        result = conversational_agent_gemini.run(initial_input)
        if result.has_failed:
            raise Exception(f"Workflow failed: {result.output}")

        result = conversational_agent_gemini.resume(
            result.execution_id,
            {"message": "Why does the sky turn red and orange during sunset?"},
        )
        if result.has_failed:
            raise Exception(f"Workflow failed: {result.output}")

        result = conversational_agent_gemini.resume(
            result.execution_id,
            {"message": "What color would the sky be on Mars?"},
        )
        if result.has_failed:
            raise Exception(f"Workflow failed: {result.output}")

        print(
            f"Total Tokens: {result.output.get('total_tokens', 0)} "
            f"(in: {result.output.get('total_input_tokens', 0)}, "
            f"out: {result.output.get('total_output_tokens', 0)})",
        )
        print(json.dumps(result.output.get("conversation_history", []), indent=2))

    except Exception as e:
        print(f"Error: {e}")
