from __future__ import annotations


from pydantic import BaseModel

from flux.task import task


def agent(
    system_prompt: str,
    *,
    model: str,
    name: str | None = None,
    tools: list[task] | None = None,
    response_format: type[BaseModel] | None = None,
    stateful: bool = False,
    max_tool_calls: int = 10,
) -> task:
    """Create a Flux @task that calls an LLM.

    Parses the model string ("provider/model_name") to select the right provider
    and returns a task that can be awaited within a Flux workflow.

    Args:
        system_prompt: The system prompt defining the agent's identity.
        model: Provider and model in "provider/model_name" format.
        name: Task name for events/traces. Defaults to "agent_{provider}_{model}".
        tools: List of Flux @task functions the agent can call as tools.
        response_format: Pydantic BaseModel subclass for structured JSON output.
        stateful: If True, accumulate message history across invocations.
        max_tool_calls: Maximum tool call iterations before forcing a final answer.

    Returns:
        A Flux @task callable with signature (instruction: str, *, context: str = "") -> str | BaseModel
    """
    if "/" not in model:
        raise ValueError(
            f"Model must be in 'provider/model_name' format, got: '{model}'. "
            "Examples: 'ollama/llama3', 'openai/gpt-4o', 'anthropic/claude-sonnet-4-20250514'",
        )

    provider, model_name = model.split("/", 1)

    if provider == "ollama":
        from flux.tasks.ai.ollama import build_ollama_agent

        return build_ollama_agent(
            system_prompt=system_prompt,
            model_name=model_name,
            name=name,
            tools=tools,
            response_format=response_format,
            stateful=stateful,
            max_tool_calls=max_tool_calls,
        )
    elif provider == "openai":
        from flux.tasks.ai.openai import build_openai_agent

        return build_openai_agent(
            system_prompt=system_prompt,
            model_name=model_name,
            name=name,
            tools=tools,
            response_format=response_format,
            stateful=stateful,
            max_tool_calls=max_tool_calls,
        )
    elif provider == "anthropic":
        from flux.tasks.ai.anthropic import build_anthropic_agent

        return build_anthropic_agent(
            system_prompt=system_prompt,
            model_name=model_name,
            name=name,
            tools=tools,
            response_format=response_format,
            stateful=stateful,
            max_tool_calls=max_tool_calls,
        )
    else:
        raise ValueError(
            f"Unknown provider: '{provider}'. " "Supported providers: ollama, openai, anthropic",
        )
