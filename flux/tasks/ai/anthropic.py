from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from flux.task import task
from flux.tasks.ai.formatter import LLMFormatter
from flux.tasks.ai.models import LLMResponse, ReasoningContent, ToolCall

try:
    from anthropic import AsyncAnthropic
except ImportError:
    AsyncAnthropic = None  # type: ignore[assignment,misc]


def build_anthropic_provider(
    model_name: str,
    max_tokens: int = 4096,
    reasoning_effort: str | None = None,
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

    formatter = AnthropicFormatter(client, model_name, max_tokens, reasoning_effort)
    return anthropic_llm, formatter


class AnthropicFormatter(LLMFormatter):
    def __init__(
        self,
        client: Any,
        model_name: str,
        max_tokens: int,
        reasoning_effort: str | None = None,
    ) -> None:
        self._client = client
        self._model_name = model_name
        self._max_tokens = max_tokens
        self._reasoning_effort = reasoning_effort

    def _convert_memory_messages(self, memory_messages: list[dict]) -> list[dict]:
        import json

        converted = []
        pending_thinking: dict | None = None
        for msg in memory_messages:
            role, content = msg["role"], msg["content"]
            if role == "thinking":
                data = json.loads(content)
                pending_thinking = data.get("opaque")
            elif role == "tool_call":
                data = json.loads(content)
                blocks: list[dict] = []
                if pending_thinking is not None:
                    blocks.append(pending_thinking)
                    pending_thinking = None
                blocks.extend(
                    {
                        "type": "tool_use",
                        "id": c["id"],
                        "name": c["name"],
                        "input": c.get("arguments", {}),
                    }
                    for c in data["calls"]
                )
                converted.append({"role": "assistant", "content": blocks})
            elif role == "tool_result":
                data = json.loads(content)
                converted.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": data["call_id"],
                                "content": data["output"],
                            },
                        ],
                    },
                )
            elif role == "assistant":
                if pending_thinking is not None:
                    blocks = [pending_thinking, {"type": "text", "text": content}]
                    pending_thinking = None
                    converted.append({"role": "assistant", "content": blocks})
                else:
                    converted.append({"role": "assistant", "content": content})
            elif role == "user":
                converted.append({"role": role, "content": content})
        if not converted:
            return converted
        merged = [converted[0]]
        for msg in converted[1:]:
            if msg.get("role") == merged[-1].get("role"):
                prev_content = merged[-1].get("content", "")
                curr_content = msg.get("content", "")
                if isinstance(prev_content, str) and isinstance(curr_content, str):
                    merged[-1]["content"] = prev_content + "\n" + curr_content
                elif isinstance(prev_content, list) and isinstance(curr_content, list):
                    merged[-1]["content"] = prev_content + curr_content
                else:
                    merged.append(msg)
            else:
                merged.append(msg)
        return merged

    def build_messages(
        self,
        system_prompt: str,
        user_content: str,
        working_memory: Any | None = None,
    ) -> tuple[list[dict], dict]:
        if working_memory:
            prior = working_memory.recall()
            messages = self._convert_memory_messages(prior) + [
                {"role": "user", "content": user_content},
            ]
        else:
            messages = [{"role": "user", "content": user_content}]

        call_kwargs: dict[str, Any] = {
            "model": self._model_name,
            "system": system_prompt,
            "max_tokens": self._max_tokens,
        }
        if self._reasoning_effort:
            call_kwargs["thinking"] = {"type": "adaptive"}
            call_kwargs["output_config"] = {"effort": self._reasoning_effort}
        return messages, call_kwargs

    def format_assistant_message(self, response: LLMResponse) -> dict:
        content: list[dict[str, Any]] = []
        if response.reasoning and response.reasoning.opaque:
            content.append(response.reasoning.opaque)
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
    reasoning: ReasoningContent | None = None
    for block in response.content:
        if block.type == "thinking":
            reasoning = ReasoningContent(
                text=block.thinking,
                opaque={
                    "type": "thinking",
                    "thinking": block.thinking,
                    "signature": getattr(block, "signature", None),
                },
            )
        elif block.type == "text":
            text_parts.append(block.text)
        elif block.type == "tool_use":
            tool_calls.append(
                ToolCall(id=block.id, name=block.name, arguments=block.input),
            )
    return LLMResponse(
        text="\n".join(text_parts) if text_parts else "",
        tool_calls=tool_calls,
        reasoning=reasoning,
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
