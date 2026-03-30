from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from flux.task import task
from flux.tasks.ai.tool_executor import (
    build_tool_schemas,
    execute_tools,
    extract_tool_calls_from_content,
    strip_tool_calls_from_content,
)

if TYPE_CHECKING:
    from flux.tasks.ai.memory.working_memory import WorkingMemory


def build_ollama_agent(
    system_prompt: str,
    model_name: str,
    name: str | None = None,
    tools: list[Any] | None = None,
    response_format: type[BaseModel] | None = None,
    working_memory: WorkingMemory | None = None,
    max_tool_calls: int = 10,
    max_concurrent_tools: int | None = None,
    stream: bool = True,
    plan_summary_fn: Any | None = None,
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
    tool_names = {s["name"] for s in tool_schemas} if tool_schemas else set()

    @task.with_options(name=task_name)
    async def ollama_agent_task(instruction: str, *, context: str = "") -> str | BaseModel:
        client = AsyncClient()
        user_content = instruction
        if context:
            user_content = f"{instruction}\n\nContext from previous work:\n\n{context}"

        if working_memory:
            prior_messages = working_memory.recall()
            call_messages = (
                [{"role": "system", "content": system_prompt}]
                + prior_messages
                + [{"role": "user", "content": user_content}]
            )
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

        from flux.tasks.progress import progress

        if stream and not ollama_tools:
            content = ""
            async for chunk in await client.chat(**{**kwargs, "stream": True}):
                token = chunk["message"]["content"]
                if token:
                    content += token
                    await progress({"token": token})
        else:
            response = await client.chat(**kwargs)
            response_message = response["message"]

            tool_call_count = 0
            tool_iteration = 0

            def _extract_tool_calls(msg: dict) -> list[dict] | None:
                """Extract tool calls from structured field or text content."""
                if msg.get("tool_calls"):
                    return [
                        {
                            "id": f"call_{i}",
                            "name": tc["function"]["name"],
                            "arguments": tc["function"]["arguments"],
                        }
                        for i, tc in enumerate(msg["tool_calls"])
                    ]
                if tool_names and msg.get("content"):
                    return extract_tool_calls_from_content(
                        msg["content"],
                        tool_names,
                    )
                return None

            pending_tool_calls = _extract_tool_calls(response_message)
            while pending_tool_calls and tools and tool_call_count < max_tool_calls:
                tool_call_count += len(pending_tool_calls)

                call_messages.append(response_message)
                results = await execute_tools(
                    pending_tool_calls,
                    tools,
                    iteration=tool_iteration,
                    max_concurrent=max_concurrent_tools,
                )
                tool_iteration += 1
                for result in results:
                    call_messages.append({"role": "tool", "content": result["output"]})

                if plan_summary_fn:
                    summary = plan_summary_fn()
                    if summary:
                        call_messages[-1]["content"] += f"\n\n{summary}"

                response = await client.chat(**{**kwargs, "messages": call_messages})
                response_message = response["message"]
                pending_tool_calls = _extract_tool_calls(response_message)

                if (
                    not pending_tool_calls
                    and not response_message.get("content")
                    and plan_summary_fn
                    and plan_summary_fn()
                    and tool_call_count < max_tool_calls
                ):
                    summary = plan_summary_fn()
                    call_messages.append(response_message)
                    call_messages.append(
                        {
                            "role": "user",
                            "content": f"Continue working on your plan. {summary}",
                        },
                    )
                    response = await client.chat(**{**kwargs, "messages": call_messages})
                    response_message = response["message"]
                    pending_tool_calls = _extract_tool_calls(response_message)

            if pending_tool_calls and tool_call_count >= max_tool_calls:
                call_messages.append(response_message)
                call_messages.append(
                    {
                        "role": "user",
                        "content": "You must provide your final answer now. Do not call any more tools.",
                    },
                )
                kwargs_no_tools = {k: v for k, v in kwargs.items() if k != "tools"}
                response = await client.chat(**{**kwargs_no_tools, "messages": call_messages})
                response_message = response["message"]

            content = response_message.get("content", "")

            if stream and not content and not pending_tool_calls:
                call_messages.append(response_message)
                kwargs_stream = {k: v for k, v in kwargs.items() if k != "tools"}
                kwargs_stream["messages"] = call_messages
                async for chunk in await client.chat(**{**kwargs_stream, "stream": True}):
                    token = chunk["message"]["content"]
                    if token:
                        content += token
                        await progress({"token": token})

            if tool_names:
                content = strip_tool_calls_from_content(content)

        if working_memory:
            await working_memory.memorize("user", user_content)
            await working_memory.memorize("assistant", content)

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
