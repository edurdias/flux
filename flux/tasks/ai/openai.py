from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from flux.task import task
from flux.tasks.ai.tool_executor import build_tool_schemas, execute_tools

if TYPE_CHECKING:
    from flux.tasks.ai.memory.working_memory import WorkingMemory

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
    working_memory: WorkingMemory | None = None,
    max_tool_calls: int = 10,
    stream: bool = True,
    plan_summary_fn: Any | None = None,
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

    @task.with_options(name=task_name)
    async def openai_agent_task(instruction: str, *, context: str = "") -> str | BaseModel:
        from flux.tasks.progress import progress

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
            tool_iteration = 0
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
                results = await execute_tools(tool_calls, tools, iteration=tool_iteration)
                tool_iteration += 1
                for tc, result in zip(tool_calls, results):
                    call_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result["output"],
                        },
                    )

                if plan_summary_fn:
                    summary = plan_summary_fn()
                    if summary:
                        call_messages[-1]["content"] += f"\n\n{summary}"

                response = await client.chat.completions.create(
                    **{**kwargs, "messages": call_messages},
                )
                message = response.choices[0].message

                if (
                    not message.tool_calls
                    and not message.content
                    and plan_summary_fn
                    and plan_summary_fn()
                    and tool_call_count < max_tool_calls
                ):
                    summary = plan_summary_fn()
                    call_messages.append(message.model_dump())
                    call_messages.append(
                        {
                            "role": "user",
                            "content": f"Continue working on your plan. {summary}",
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

            content = message.content or ""

            if stream and not content and not message.tool_calls:
                call_messages.append(message.model_dump())
                kwargs_stream = {k: v for k, v in kwargs.items() if k != "tools"}
                kwargs_stream["messages"] = call_messages
                async for chunk in await client.chat.completions.create(
                    **kwargs_stream,
                    stream=True,
                ):
                    token = chunk.choices[0].delta.content
                    if token:
                        content += token
                        await progress({"token": token})

        if working_memory:
            await working_memory.memorize("user", user_content)
            await working_memory.memorize("assistant", content)

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
