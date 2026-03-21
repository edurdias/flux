from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from flux.task import task
from flux.tasks.ai.tool_executor import build_tool_schemas, execute_tools

if TYPE_CHECKING:
    from flux.tasks.ai.memory.working_memory import WorkingMemory

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None  # type: ignore[assignment,misc]
    types = None  # type: ignore[assignment,misc]


def build_gemini_agent(
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
    """Build a Flux @task that calls Google Gemini's API."""
    if genai is None:
        raise ImportError(
            "To use Google Gemini models, install the google-genai package: "
            "pip install google-genai",
        )

    task_name = name or f"agent_google_{model_name.replace('-', '_').replace('.', '_')}"
    tool_schemas = build_tool_schemas(tools) if tools else None
    gemini_tools = _to_gemini_tools(tool_schemas) if tool_schemas else None

    @task.with_options(name=task_name)
    async def gemini_agent_task(instruction: str, *, context: str = "") -> str | BaseModel:
        client = genai.Client()
        from flux.tasks.progress import progress

        user_content = instruction
        if context:
            user_content = f"{instruction}\n\nContext from previous work:\n\n{context}"

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=max_tokens,
            tools=gemini_tools,
            response_mime_type="application/json" if response_format else None,
            response_schema=response_format if response_format else None,
        )

        if working_memory:
            prior_messages = [_to_content(m["role"], m["content"]) for m in working_memory.recall()]
            contents = prior_messages + [_to_content("user", user_content)]
        else:
            contents = [_to_content("user", user_content)]

        if stream and not gemini_tools:
            content = ""
            async for chunk in await client.aio.models.generate_content_stream(
                model=model_name,
                contents=contents,
                config=config,
            ):
                token = chunk.text
                if token:
                    content += token
                    await progress({"token": token})
        else:
            response = await client.aio.models.generate_content(
                model=model_name,
                contents=contents,
                config=config,
            )

            tool_call_count = 0
            tool_iteration = 0
            while response.function_calls and tools and tool_call_count < max_tool_calls:
                tool_calls = [
                    {
                        "id": fc.name,
                        "name": fc.name,
                        "arguments": fc.args,
                    }
                    for fc in response.function_calls
                ]
                tool_call_count += len(tool_calls)

                contents.append(response.candidates[0].content)
                results = await execute_tools(tool_calls, tools, iteration=tool_iteration)
                tool_iteration += 1

                function_response_parts = [
                    types.Part(
                        function_response=types.FunctionResponse(
                            name=tc["name"],
                            response={"output": result["output"]},
                        ),
                    )
                    for tc, result in zip(tool_calls, results)
                ]
                contents.append(
                    types.Content(role="user", parts=function_response_parts),
                )

                response = await client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config,
                )

            if response.function_calls and tool_call_count >= max_tool_calls:
                contents.append(response.candidates[0].content)
                contents.append(
                    _to_content(
                        "user",
                        "You must provide your final answer now. Do not call any more tools.",
                    ),
                )
                config_no_tools = types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    max_output_tokens=max_tokens,
                )
                response = await client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config_no_tools,
                )

            if stream and not (response.function_calls):
                config_stream = types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    max_output_tokens=max_tokens,
                )
                content = ""
                async for chunk in await client.aio.models.generate_content_stream(
                    model=model_name,
                    contents=contents,
                    config=config_stream,
                ):
                    token = chunk.text
                    if token:
                        content += token
                        await progress({"token": token})
            else:
                content = response.text or ""

        if working_memory:
            await working_memory.memorize("user", user_content)
            await working_memory.memorize("assistant", content)

        if response_format:
            return response_format.model_validate_json(content)

        return content

    return gemini_agent_task


def _to_content(role: str, text: str) -> types.Content:
    gemini_role = "model" if role == "assistant" else role
    return types.Content(role=gemini_role, parts=[types.Part(text=text)])


def _to_gemini_tools(schemas: list[dict[str, Any]]) -> list[types.Tool]:
    declarations = [
        types.FunctionDeclaration(
            name=s["name"],
            description=s["description"],
            parameters_json_schema=s["parameters"],
        )
        for s in schemas
    ]
    return [types.Tool(function_declarations=declarations)]
