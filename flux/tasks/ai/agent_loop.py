from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from flux.tasks.ai.models import LLMResponse
from flux.tasks.ai.tool_executor import execute_tools

if TYPE_CHECKING:
    from flux.task import task as TaskType
    from flux.tasks.ai.formatter import LLMFormatter
    from flux.tasks.ai.memory.working_memory import WorkingMemory

logger = logging.getLogger("flux.agent")


async def _fire_hooks(hooks: list[Any] | None, agent_name: str, value: Any) -> None:
    if not hooks:
        return
    for hook in hooks:
        try:
            await hook(agent_name, value)
        except Exception:
            logger.warning("Hook %s failed", hook, exc_info=True)


async def run_agent_loop(
    *,
    llm_task: TaskType,
    formatter: LLMFormatter,
    system_prompt: str,
    instruction: str,
    context: str = "",
    tools: list[Any] | None = None,
    tool_schemas: list[Any] | None = None,
    response_format: type[BaseModel] | None = None,
    working_memory: WorkingMemory | None = None,
    max_tool_calls: int = 10,
    max_concurrent_tools: int | None = None,
    stream: bool = False,
    plan_summary_fn: Any | None = None,
    approval_mode: str = "default",
    on_complete: list[Any] | None = None,
    on_pause: list[Any] | None = None,
    agent_name: str = "agent",
) -> str | BaseModel:
    from flux.tasks.progress import progress

    user_content = instruction
    if context:
        user_content = f"{instruction}\n\nContext from previous work:\n\n{context}"

    sys_prompt = system_prompt
    if response_format and not tools:
        schema_json = json.dumps(response_format.model_json_schema())
        sys_prompt += f"\n\nRespond with JSON matching this schema:\n{schema_json}"

    messages, call_kwargs = formatter.build_messages(sys_prompt, user_content, working_memory)

    if tool_schemas:
        call_kwargs["tools"] = tool_schemas

    call_counter = 0

    if stream and not tools:
        content = ""
        async for token in formatter.stream(messages, call_kwargs):
            content += token
            await progress({"token": token})

        if working_memory:
            await working_memory.memorize("user", user_content)
            await working_memory.memorize("assistant", content)

        if response_format:
            return_value = response_format.model_validate_json(content)
            await _fire_hooks(on_complete, agent_name, return_value)
            return return_value

        await _fire_hooks(on_complete, agent_name, content)
        return content

    result = await llm_task.with_options(name=f"llm_{call_counter}")(messages, **call_kwargs)
    call_counter += 1
    response = _ensure_llm_response(result)

    always_approved: set[str] = set()

    tool_call_count = 0
    tool_iteration = 0

    while response.tool_calls and tools and tool_call_count < max_tool_calls:
        tool_call_count += len(response.tool_calls)

        messages.append(formatter.format_assistant_message(response))

        tool_call_dicts = [tc.model_dump() for tc in response.tool_calls]
        results = await execute_tools(
            tool_call_dicts,
            tools,
            iteration=tool_iteration,
            max_concurrent=max_concurrent_tools,
            always_approved=always_approved,
            approval_mode=approval_mode,
        )
        tool_iteration += 1

        tool_result_messages = formatter.format_tool_results(response.tool_calls, results)

        if plan_summary_fn:
            summary = plan_summary_fn()
            if summary and tool_result_messages:
                last = tool_result_messages[-1]
                if isinstance(last, dict) and isinstance(last.get("content"), str):
                    last["content"] += f"\n\n{summary}"
                else:
                    tool_result_messages.append(
                        formatter.format_user_message(summary),
                    )

        messages.extend(tool_result_messages)

        result = await llm_task.with_options(name=f"llm_{call_counter}")(messages, **call_kwargs)
        call_counter += 1
        response = _ensure_llm_response(result)

        if (
            not response.tool_calls
            and not response.text
            and plan_summary_fn
            and plan_summary_fn()
            and tool_call_count < max_tool_calls
        ):
            summary = plan_summary_fn()
            messages.append(formatter.format_assistant_message(response))
            messages.append(
                formatter.format_user_message(f"Continue working on your plan. {summary}"),
            )
            result = await llm_task.with_options(name=f"llm_{call_counter}")(
                messages,
                **call_kwargs,
            )
            call_counter += 1
            response = _ensure_llm_response(result)

    if response.tool_calls and tool_call_count >= max_tool_calls:
        messages.append(formatter.format_assistant_message(response))
        messages.append(
            formatter.format_user_message(
                "You must provide your final answer now. Do not call any more tools.",
            ),
        )
        no_tool_kwargs = formatter.remove_tools_from_kwargs(call_kwargs)
        result = await llm_task.with_options(name=f"llm_{call_counter}")(messages, **no_tool_kwargs)
        call_counter += 1
        response = _ensure_llm_response(result)

    content = response.text

    if stream and not content and not response.tool_calls:
        messages.append(formatter.format_assistant_message(response))
        no_tool_kwargs = formatter.remove_tools_from_kwargs(call_kwargs)
        content = ""
        async for token in formatter.stream(messages, no_tool_kwargs):
            content += token
            await progress({"token": token})

    if working_memory:
        await working_memory.memorize("user", user_content)
        await working_memory.memorize("assistant", content)

    if response_format:
        return_value = response_format.model_validate_json(content)
        await _fire_hooks(on_complete, agent_name, return_value)
        return return_value

    await _fire_hooks(on_complete, agent_name, content)
    return content


def _ensure_llm_response(result: Any) -> LLMResponse:
    if isinstance(result, LLMResponse):
        return result
    if isinstance(result, dict):
        return LLMResponse.model_validate(result)
    return LLMResponse(text=str(result))
