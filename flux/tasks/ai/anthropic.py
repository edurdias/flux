from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from flux.task import task
from flux.tasks.ai.tool_executor import build_tool_schemas, execute_tools


def build_anthropic_agent(
    system_prompt: str,
    model_name: str,
    name: str | None = None,
    tools: list[Any] | None = None,
    response_format: type[BaseModel] | None = None,
    stateful: bool = False,
    max_tool_calls: int = 10,
    max_tokens: int = 4096,
) -> task:
    """Build a Flux @task that calls Anthropic's messages API."""
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        raise ImportError(
            "To use anthropic models, install the anthropic package: pip install anthropic",
        ) from None

    task_name = name or f"agent_anthropic_{model_name.replace('-', '_').replace('.', '_')}"
    tool_schemas = build_tool_schemas(tools) if tools else None
    anthropic_tools = _to_anthropic_tools(tool_schemas) if tool_schemas else None

    client = AsyncAnthropic()
    messages: list[dict[str, Any]] = []

    @task.with_options(name=task_name)
    async def anthropic_agent_task(instruction: str, *, context: str = "") -> str | BaseModel:
        user_content = instruction
        if context:
            user_content = f"{instruction}\n\nContext from previous work:\n\n{context}"

        if stateful:
            messages.append({"role": "user", "content": user_content})
            call_messages = list(messages)
        else:
            call_messages = [{"role": "user", "content": user_content}]

        sys_prompt = system_prompt
        if response_format and not tools:
            schema_json = json.dumps(response_format.model_json_schema())
            sys_prompt += f"\n\nRespond with JSON matching this schema:\n{schema_json}"

        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": call_messages,
            "system": sys_prompt,
            "max_tokens": max_tokens,
        }
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools

        response = await client.messages.create(**kwargs)

        tool_call_count = 0
        while _has_tool_use(response) and tools and tool_call_count < max_tool_calls:
            tool_calls = [
                {
                    "id": block.id,
                    "name": block.name,
                    "arguments": block.input,
                }
                for block in response.content
                if block.type == "tool_use"
            ]
            tool_call_count += len(tool_calls)

            call_messages.append(
                {"role": "assistant", "content": _serialize_content(response.content)},
            )
            results = await execute_tools(tool_calls, tools)
            tool_results = [
                {
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": result["output"],
                }
                for tc, result in zip(tool_calls, results)
            ]
            call_messages.append({"role": "user", "content": tool_results})

            response = await client.messages.create(**{**kwargs, "messages": call_messages})

        if _has_tool_use(response) and tool_call_count >= max_tool_calls:
            call_messages.append(
                {"role": "assistant", "content": _serialize_content(response.content)},
            )
            call_messages.append(
                {
                    "role": "user",
                    "content": "You must provide your final answer now. Do not call any more tools.",
                },
            )
            kwargs_no_tools = {k: v for k, v in kwargs.items() if k != "tools"}
            response = await client.messages.create(
                **{**kwargs_no_tools, "messages": call_messages},
            )

        content = _extract_text(response)

        if stateful:
            messages.append({"role": "assistant", "content": content})

        if response_format:
            return response_format.model_validate_json(content)

        return content

    return anthropic_agent_task


def _has_tool_use(response) -> bool:
    return any(block.type == "tool_use" for block in response.content)


def _extract_text(response) -> str:
    text_blocks = [block.text for block in response.content if block.type == "text"]
    return "\n".join(text_blocks) if text_blocks else ""


def _serialize_content(content) -> list[dict[str, Any]]:
    result = []
    for block in content:
        if block.type == "text":
            result.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            result.append(
                {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input},
            )
    return result


def _to_anthropic_tools(schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": s["name"],
            "description": s["description"],
            "input_schema": s["parameters"],
        }
        for s in schemas
    ]
