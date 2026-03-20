from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from flux.task import task
from flux.tasks.ai.tool_executor import build_tool_schemas, execute_tools

if TYPE_CHECKING:
    from flux.tasks.ai.memory.working_memory import WorkingMemory

try:
    from anthropic import AsyncAnthropic
except ImportError:
    AsyncAnthropic = None  # type: ignore[assignment,misc]


def build_anthropic_agent(
    system_prompt: str,
    model_name: str,
    name: str | None = None,
    tools: list[Any] | None = None,
    response_format: type[BaseModel] | None = None,
    working_memory: WorkingMemory | None = None,
    max_tool_calls: int = 10,
    max_tokens: int = 4096,
    stream: bool = True,
) -> task:
    """Build a Flux @task that calls Anthropic's messages API."""
    if AsyncAnthropic is None:
        raise ImportError(
            "To use anthropic models, install the anthropic package: pip install anthropic",
        )

    task_name = name or f"agent_anthropic_{model_name.replace('-', '_').replace('.', '_')}"
    tool_schemas = build_tool_schemas(tools) if tools else None
    anthropic_tools = _to_anthropic_tools(tool_schemas) if tool_schemas else None

    client = AsyncAnthropic()

    @task.with_options(name=task_name)
    async def anthropic_agent_task(instruction: str, *, context: str = "") -> str | BaseModel:
        from flux.tasks.progress import progress

        user_content = instruction
        if context:
            user_content = f"{instruction}\n\nContext from previous work:\n\n{context}"

        call_messages: list[dict[str, Any]]
        if working_memory:
            prior_messages = working_memory.recall()
            call_messages = prior_messages + [{"role": "user", "content": user_content}]
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

        if stream and not anthropic_tools:
            async with client.messages.stream(**kwargs) as stream_ctx:
                async for text in stream_ctx.text_stream:
                    await progress({"token": text})
                response = stream_ctx.get_final_message()
        else:
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

            if stream and not _has_tool_use(response):
                kwargs_stream = {k: v for k, v in kwargs.items() if k != "tools"}
                kwargs_stream["messages"] = call_messages
                async with client.messages.stream(**kwargs_stream) as stream_ctx:
                    async for text in stream_ctx.text_stream:
                        await progress({"token": text})
                    response = stream_ctx.get_final_message()

        content = _extract_text(response)

        if working_memory:
            await working_memory.memorize("user", user_content)
            await working_memory.memorize("assistant", content)

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
