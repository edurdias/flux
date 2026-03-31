from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from flux.task import task
from flux.tasks.ai.formatter import LLMFormatter
from flux.tasks.ai.models import LLMResponse, ToolCall

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None  # type: ignore[assignment,misc]


def build_openai_provider(
    model_name: str,
    response_format: Any | None = None,
) -> tuple[task, LLMFormatter]:
    if AsyncOpenAI is None:
        raise ImportError(
            "To use openai models, install the openai package: pip install openai",
        )

    client = AsyncOpenAI()

    @task.with_options(name="openai_llm")
    async def openai_llm(messages: list[dict], **kwargs: Any) -> LLMResponse:
        response = await client.chat.completions.create(
            messages=messages,
            **kwargs,
        )
        return _to_llm_response(response)

    formatter = OpenAIFormatter(client, model_name, response_format)
    return openai_llm, formatter


class OpenAIFormatter(LLMFormatter):
    def __init__(
        self,
        client: Any,
        model_name: str,
        response_format: Any | None = None,
    ) -> None:
        self._client = client
        self._model_name = model_name
        self._response_format = response_format

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
                + prior
                + [{"role": "user", "content": user_content}]
            )
        else:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]

        call_kwargs: dict[str, Any] = {"model": self._model_name}

        if self._response_format:
            call_kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": self._response_format.__name__,
                    "schema": self._response_format.model_json_schema(),
                },
            }

        return messages, call_kwargs

    def format_assistant_message(self, response: LLMResponse) -> dict:
        msg: dict[str, Any] = {"role": "assistant", "content": response.text or None}
        if response.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
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
                "tool_call_id": tc.id,
                "content": result["output"],
            }
            for tc, result in zip(tool_calls, results)
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
        async for chunk in await self._client.chat.completions.create(
            messages=messages,
            **call_kwargs,
            stream=True,
        ):
            token = chunk.choices[0].delta.content
            if token:
                yield token


def _to_llm_response(response: Any) -> LLMResponse:
    message = response.choices[0].message
    text = message.content or ""
    tool_calls: list[ToolCall] = []
    if message.tool_calls:
        for tc in message.tool_calls:
            tool_calls.append(
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments),
                ),
            )
    return LLMResponse(text=text, tool_calls=tool_calls)


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
