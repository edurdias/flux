from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from flux.task import task
from flux.tasks.ai.tool_executor import build_tool_schemas, execute_tools


def build_ollama_agent(
    system_prompt: str,
    model_name: str,
    name: str | None = None,
    tools: list[Any] | None = None,
    response_format: type[BaseModel] | None = None,
    stateful: bool = False,
    max_tool_calls: int = 10,
) -> task:
    """Build a Flux @task that calls Ollama's chat API."""
    try:
        from ollama import AsyncClient
    except ImportError:
        raise ImportError(
            "To use ollama models, install the ollama package: pip install ollama",
        ) from None

    task_name = name or f"agent_ollama_{model_name.replace(':', '_').replace('.', '_')}"
    tool_schemas = build_tool_schemas(tools) if tools else None
    ollama_tools = _to_ollama_tools(tool_schemas) if tool_schemas else None

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]

    @task.with_options(name=task_name)
    async def ollama_agent_task(instruction: str, *, context: str = "") -> str | BaseModel:
        client = AsyncClient()

        user_content = instruction
        if context:
            user_content = f"{instruction}\n\nContext from previous work:\n\n{context}"

        if stateful:
            messages.append({"role": "user", "content": user_content})
            call_messages = list(messages)
        else:
            call_messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]

        if response_format and not tools:
            schema_json = json.dumps(response_format.model_json_schema())
            call_messages[-1][
                "content"
            ] += f"\n\nRespond with JSON matching this schema:\n{schema_json}"

        kwargs: dict[str, Any] = {"model": model_name, "messages": call_messages}
        if ollama_tools:
            kwargs["tools"] = ollama_tools
        if response_format and not tools:
            kwargs["format"] = "json"

        response = await client.chat(**kwargs)
        response_message = response["message"]

        tool_call_count = 0
        while response_message.get("tool_calls") and tools and tool_call_count < max_tool_calls:
            tool_calls = [
                {
                    "id": f"call_{i}",
                    "name": tc["function"]["name"],
                    "arguments": tc["function"]["arguments"],
                }
                for i, tc in enumerate(response_message["tool_calls"])
            ]
            tool_call_count += len(tool_calls)

            call_messages.append(response_message)
            results = await execute_tools(tool_calls, tools)
            for result in results:
                call_messages.append({"role": "tool", "content": result["output"]})

            response = await client.chat(**{**kwargs, "messages": call_messages})
            response_message = response["message"]

        if response_message.get("tool_calls") and tool_call_count >= max_tool_calls:
            call_messages.append(response_message)
            call_messages.append(
                {
                    "role": "user",
                    "content": "You must provide your final answer now. Do not call any more tools.",
                },
            )
            response = await client.chat(model=model_name, messages=call_messages)
            response_message = response["message"]

        content = response_message["content"]

        if stateful:
            messages.append({"role": "assistant", "content": content})

        if response_format:
            return response_format.model_validate_json(content)

        return content

    return ollama_agent_task


def _to_ollama_tools(schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": s["name"],
                "description": s["description"],
                "parameters": s["parameters"],
            },
        }
        for s in schemas
    ]
