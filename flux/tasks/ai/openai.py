from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from flux.task import task
from flux.tasks.ai.tool_executor import build_tool_schemas, execute_tools

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None  # type: ignore[assignment,misc]


def build_openai_agent(
    system_prompt: str,
    model_name: str,
    name: str | None = None,
    tools: list[Any] | None = None,
    response_format: type[BaseModel] | None = None,
    stateful: bool = False,
    max_tool_calls: int = 10,
    stream: bool = True,
) -> task:
    """Build a Flux @task that calls OpenAI's chat API."""
    if AsyncOpenAI is None:
        raise ImportError(
            "To use openai models, install the openai package: pip install openai",
        )

    task_name = name or f"agent_openai_{model_name.replace('-', '_').replace('.', '_')}"
    tool_schemas = build_tool_schemas(tools) if tools else None
    openai_tools = _to_openai_tools(tool_schemas) if tool_schemas else None

    client = AsyncOpenAI()
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]

    @task.with_options(name=task_name)
    async def openai_agent_task(instruction: str, *, context: str = "") -> str | BaseModel:
        from flux.tasks.progress import progress

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

        kwargs: dict[str, Any] = {"model": model_name, "messages": call_messages}
        if openai_tools:
            kwargs["tools"] = openai_tools
        if response_format:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_format.__name__,
                    "schema": response_format.model_json_schema(),
                },
            }

        if stream and not openai_tools:
            content = ""
            async for chunk in await client.chat.completions.create(**kwargs, stream=True):
                token = chunk.choices[0].delta.content
                if token:
                    content += token
                    await progress({"token": token})
        else:
            response = await client.chat.completions.create(**kwargs)
            message = response.choices[0].message

            tool_call_count = 0
            while message.tool_calls and tools and tool_call_count < max_tool_calls:
                tool_calls = [
                    {
                        "id": tc.id,
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    }
                    for tc in message.tool_calls
                ]
                tool_call_count += len(tool_calls)

                call_messages.append(message.model_dump())
                results = await execute_tools(tool_calls, tools)
                for tc, result in zip(tool_calls, results):
                    call_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result["output"],
                        },
                    )

                response = await client.chat.completions.create(
                    **{**kwargs, "messages": call_messages},
                )
                message = response.choices[0].message

            if message.tool_calls and tool_call_count >= max_tool_calls:
                call_messages.append(message.model_dump())
                call_messages.append(
                    {
                        "role": "user",
                        "content": "You must provide your final answer now. Do not call any more tools.",
                    },
                )
                kwargs_no_tools = {k: v for k, v in kwargs.items() if k != "tools"}
                response = await client.chat.completions.create(
                    **{**kwargs_no_tools, "messages": call_messages},
                )
                message = response.choices[0].message

            if stream and not message.tool_calls:
                kwargs_stream = {k: v for k, v in kwargs.items() if k != "tools"}
                kwargs_stream["messages"] = call_messages
                content = ""
                async for chunk in await client.chat.completions.create(
                    **kwargs_stream,
                    stream=True,
                ):
                    token = chunk.choices[0].delta.content
                    if token:
                        content += token
                        await progress({"token": token})
            else:
                content = message.content or ""

        if stateful:
            messages.append({"role": "assistant", "content": content})

        if response_format:
            return response_format.model_validate_json(content)

        return content

    return openai_agent_task


def _to_openai_tools(schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
