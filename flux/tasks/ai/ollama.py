from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from flux.task import task
from flux.tasks.ai.formatter import LLMFormatter
from flux.tasks.ai.models import LLMResponse, ReasoningContent, ToolCall
from flux.tasks.ai.tool_executor import (
    extract_tool_calls_from_content,
    strip_tool_calls_from_content,
)

try:
    from ollama import AsyncClient
except ImportError:
    AsyncClient = None  # type: ignore[assignment,misc]


def build_ollama_provider(
    model_name: str,
    response_format: Any | None = None,
    reasoning_effort: str | None = None,
) -> tuple[task, LLMFormatter]:
    if AsyncClient is None:
        raise ImportError(
            "To use ollama models, install the ollama package: pip install ollama",
        )

    client = AsyncClient()

    @task.with_options(name="ollama_llm")
    async def ollama_llm(
        messages: list[dict],
        **kwargs: Any,
    ) -> LLMResponse:
        tool_names: set[str] = kwargs.pop("tool_names", set())
        response = await client.chat(messages=messages, **kwargs)
        return _to_llm_response(response, tool_names)

    formatter = OllamaFormatter(model_name, client, response_format, reasoning_effort)
    return ollama_llm, formatter


class OllamaFormatter(LLMFormatter):
    supports_reasoning_stream = True

    def __init__(
        self,
        model_name: str,
        client: Any = None,
        response_format: Any | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        self._client = client
        self._model_name = model_name
        self._response_format = response_format
        self._reasoning_effort = reasoning_effort
        self._tool_names: set[str] = set()

    def set_tool_names(self, tool_names: set[str]) -> None:
        self._tool_names = tool_names

    def _convert_memory_messages(self, memory_messages: list[dict]) -> list[dict]:
        converted = []
        for msg in memory_messages:
            role, content = msg["role"], msg["content"]
            if role == "tool_call":
                data = json.loads(content)
                converted.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"function": {"name": c["name"], "arguments": c.get("arguments", {})}}
                            for c in data["calls"]
                        ],
                    },
                )
            elif role == "tool_result":
                data = json.loads(content)
                converted.append({"role": "tool", "content": data["output"]})
            elif role == "reasoning":
                continue
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
            messages = (
                [{"role": "system", "content": system_prompt}]
                + self._convert_memory_messages(prior)
                + [{"role": "user", "content": user_content}]
            )
        else:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]

        call_kwargs: dict[str, Any] = {"model": self._model_name}

        if self._response_format and not self._tool_names:
            schema_json = json.dumps(self._response_format.model_json_schema())
            messages[-1]["content"] += f"\n\nRespond with JSON matching this schema:\n{schema_json}"
            call_kwargs["format"] = "json"

        if self._tool_names:
            call_kwargs["tool_names"] = self._tool_names

        if self._reasoning_effort is not None:
            call_kwargs["think"] = True

        return messages, call_kwargs

    def format_assistant_message(self, response: LLMResponse) -> dict:
        msg: dict[str, Any] = {"role": "assistant", "content": response.text or ""}
        if response.tool_calls:
            msg["tool_calls"] = [
                {
                    "function": {
                        "name": tc.name,
                        "arguments": tc.arguments,
                    },
                }
                for tc in response.tool_calls
            ]
        return msg

    def format_tool_results(
        self,
        tool_calls: list[ToolCall],
        results: list[dict],
    ) -> list[dict]:
        return [
            {
                "role": "tool",
                "content": result["output"],
            }
            for result in results
        ]

    def format_user_message(self, text: str) -> dict:
        return {"role": "user", "content": text}

    def remove_tools_from_kwargs(self, call_kwargs: dict) -> dict:
        return {k: v for k, v in call_kwargs.items() if k != "tools"}

    async def stream(
        self,
        messages: list[dict],
        call_kwargs: dict,
    ) -> AsyncIterator[str]:
        stream_kwargs = {k: v for k, v in call_kwargs.items() if k != "tool_names"}
        async for chunk in await self._client.chat(
            messages=messages,
            **stream_kwargs,
            stream=True,
        ):
            token = chunk["message"]["content"]
            if token:
                yield token

    async def call_with_reasoning_stream(
        self,
        messages: list[dict],
        call_kwargs: dict,
        on_reasoning_token: Any,
    ) -> LLMResponse:
        stream_kwargs = {k: v for k, v in call_kwargs.items() if k != "tool_names"}
        tool_names = call_kwargs.get("tool_names", set())

        thinking_parts: list[str] = []
        content_parts: list[str] = []
        tool_calls_raw: list[dict] = []

        async for chunk in await self._client.chat(
            messages=messages,
            **stream_kwargs,
            stream=True,
        ):
            msg = chunk.get("message", {})
            if msg.get("thinking"):
                thinking_parts.append(msg["thinking"])
                await on_reasoning_token(msg["thinking"])
            if msg.get("content"):
                content_parts.append(msg["content"])
            if msg.get("tool_calls"):
                tool_calls_raw.extend(msg["tool_calls"])

        content = "".join(content_parts)
        thinking = "".join(thinking_parts) or None

        tool_calls: list[ToolCall] = []
        if tool_calls_raw:
            for tc in tool_calls_raw:
                # Ollama doesn't return tool_call IDs. Generate a uuid each
                # time so memory and replay layers keyed by ID stay unique
                # across the multiple LLM turns of an agent loop — earlier
                # versions reused `call_{i}` per turn and collided.
                tool_calls.append(
                    ToolCall(
                        id=f"call_{uuid4().hex}",
                        name=tc["function"]["name"],
                        arguments=tc["function"]["arguments"],
                    ),
                )
        elif tool_names and content:
            extracted = extract_tool_calls_from_content(content, tool_names)
            if extracted:
                tool_calls = [
                    ToolCall(id=tc["id"], name=tc["name"], arguments=tc["arguments"])
                    for tc in extracted
                ]
                content = strip_tool_calls_from_content(content)

        reasoning = ReasoningContent(text=thinking, opaque=None) if thinking else None
        return LLMResponse(text=content, tool_calls=tool_calls, reasoning=reasoning)


def _to_llm_response(
    response: dict,
    tool_names: set[str] | None = None,
) -> LLMResponse:
    message = response["message"]
    content = message.get("content", "")
    tool_calls: list[ToolCall] = []

    if message.get("tool_calls"):
        for tc in message["tool_calls"]:
            tool_calls.append(
                ToolCall(
                    id=f"call_{uuid4().hex}",
                    name=tc["function"]["name"],
                    arguments=tc["function"]["arguments"],
                ),
            )
    elif tool_names and content:
        extracted = extract_tool_calls_from_content(content, tool_names)
        if extracted:
            tool_calls = [
                ToolCall(
                    id=tc["id"],
                    name=tc["name"],
                    arguments=tc["arguments"],
                )
                for tc in extracted
            ]
            content = strip_tool_calls_from_content(content)

    thinking = message.get("thinking")
    reasoning = ReasoningContent(text=thinking, opaque=None) if thinking else None

    return LLMResponse(text=content, tool_calls=tool_calls, reasoning=reasoning)


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
