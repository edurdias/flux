from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from flux.task import task
from flux.tasks.ai.formatter import LLMFormatter
from flux.tasks.ai.models import LLMResponse, ToolCall

try:
    from anthropic import AsyncAnthropic
except ImportError:
    AsyncAnthropic = None  # type: ignore[assignment,misc]


def build_anthropic_provider(
    model_name: str,
    max_tokens: int = 4096,
) -> tuple[task, LLMFormatter]:
    if AsyncAnthropic is None:
        raise ImportError(
            "To use anthropic models, install the anthropic package: pip install anthropic",
        )

    client = AsyncAnthropic()

    @task.with_options(name="anthropic_llm")
    async def anthropic_llm(messages: list[dict], **kwargs: Any) -> LLMResponse:
        response = await client.messages.create(
            messages=messages,
            **kwargs,
        )
        return _to_llm_response(response)

    formatter = AnthropicFormatter(client, model_name, max_tokens)
    return anthropic_llm, formatter


class AnthropicFormatter(LLMFormatter):
    def __init__(self, client: Any, model_name: str, max_tokens: int) -> None:
        self._client = client
        self._model_name = model_name
        self._max_tokens = max_tokens

    def _convert_memory_messages(self, memory_messages: list[dict]) -> list[dict]:
        import json

        converted = []
        for msg in memory_messages:
            role, content = msg["role"], msg["content"]
            if role == "tool_call":
                data = json.loads(content)
                blocks = [
                    {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c.get("arguments", {})}
                    for c in data["calls"]
                ]
                converted.append({"role": "assistant", "content": blocks})
            elif role == "tool_result":
                data = json.loads(content)
                converted.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": data["call_id"], "content": data["output"]}],
                })
            elif role in ("user", "assistant"):
                converted.append({"role": role, "content": content})
        return converted

    def build_messages(
        self,
        system_prompt: str,
        user_content: str,
        working_memory: Any | None = None,
    ) -> tuple[list[dict], dict]:
        if working_memory:
            prior = working_memory.recall()
            messages = self._convert_memory_messages(prior) + [{"role": "user", "content": user_content}]
        else:
            messages = [{"role": "user", "content": user_content}]

        call_kwargs: dict[str, Any] = {
            "model": self._model_name,
            "system": system_prompt,
            "max_tokens": self._max_tokens,
        }
        return messages, call_kwargs

    def format_assistant_message(self, response: LLMResponse) -> dict:
        content: list[dict[str, Any]] = []
        if response.text:
            content.append({"type": "text", "text": response.text})
        for tc in response.tool_calls:
            content.append(
                {
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.arguments,
                },
            )
        return {"role": "assistant", "content": content}

    def format_tool_results(
        self,
        tool_calls: list[ToolCall],
        results: list[dict],
    ) -> list[dict]:
        tool_results = [
            {
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": result["output"],
            }
            for tc, result in zip(tool_calls, results)
        ]
        return [{"role": "user", "content": tool_results}]

    def format_user_message(self, text: str) -> dict:
        return {"role": "user", "content": text}

    def remove_tools_from_kwargs(self, call_kwargs: dict) -> dict:
        return {k: v for k, v in call_kwargs.items() if k != "tools"}

    async def stream(
        self,
        messages: list[dict],
        call_kwargs: dict,
    ) -> AsyncIterator[str]:
        async with self._client.messages.stream(messages=messages, **call_kwargs) as ctx:
            async for text in ctx.text_stream:
                yield text


def _to_llm_response(response: Any) -> LLMResponse:
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in response.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "tool_use":
            tool_calls.append(
                ToolCall(id=block.id, name=block.name, arguments=block.input),
            )
    return LLMResponse(
        text="\n".join(text_parts) if text_parts else "",
        tool_calls=tool_calls,
    )


def _to_anthropic_tools(schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": s["name"],
            "description": s["description"],
            "input_schema": s["parameters"],
        }
        for s in schemas
    ]
