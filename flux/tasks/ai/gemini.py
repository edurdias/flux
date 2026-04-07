from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel

from flux.task import task
from flux.tasks.ai.formatter import LLMFormatter
from flux.tasks.ai.models import LLMResponse, ToolCall

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None  # type: ignore[assignment,misc]
    types = None  # type: ignore[assignment,misc]


def build_gemini_provider(
    model_name: str,
    max_tokens: int = 4096,
    response_format: type[BaseModel] | None = None,
) -> tuple[task, LLMFormatter]:
    if genai is None:
        raise ImportError(
            "To use Google Gemini models, install the google-genai package: "
            "pip install google-genai",
        )

    @task.with_options(name="gemini_llm")
    async def gemini_llm(
        contents: list,
        *,
        config: Any = None,
        model: str = "",
        tools: list | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        client = genai.Client()
        if tools is not None and config is not None:
            config = _config_with_tools(config, tools)
        response = await client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )
        return _to_llm_response(response)

    formatter = GeminiFormatter(model_name, max_tokens, response_format)
    return gemini_llm, formatter


class GeminiFormatter(LLMFormatter):
    def __init__(
        self,
        model_name: str,
        max_tokens: int,
        response_format: type[BaseModel] | None = None,
    ) -> None:
        self._model_name = model_name
        self._max_tokens = max_tokens
        self._response_format = response_format

    def _convert_memory_messages(self, memory_messages: list[dict]) -> list:
        import json
        from google.genai import types as _types

        converted = []
        for msg in memory_messages:
            role, content = msg["role"], msg["content"]
            if role == "tool_call":
                data = json.loads(content)
                parts = [
                    _types.Part(
                        function_call=_types.FunctionCall(name=c["name"], args=c.get("arguments", {}))
                    )
                    for c in data["calls"]
                ]
                converted.append(_types.Content(role="model", parts=parts))
            elif role == "tool_result":
                data = json.loads(content)
                converted.append(
                    _types.Content(
                        role="user",
                        parts=[
                            _types.Part(
                                function_response=_types.FunctionResponse(
                                    name=data["name"],
                                    response={"output": data["output"]},
                                )
                            )
                        ],
                    )
                )
            elif role in ("user", "assistant"):
                converted.append(_to_content(role, content))
        return converted

    def build_messages(
        self,
        system_prompt: str,
        user_content: str,
        working_memory: Any | None = None,
    ) -> tuple[list, dict]:
        if working_memory:
            prior = working_memory.recall()
            contents = self._convert_memory_messages(prior)
            contents.append(_to_content("user", user_content))
        else:
            contents = [_to_content("user", user_content)]

        config_kwargs: dict[str, Any] = {
            "system_instruction": system_prompt,
            "max_output_tokens": self._max_tokens,
        }
        if self._response_format:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = self._response_format

        config = types.GenerateContentConfig(**config_kwargs)

        call_kwargs: dict[str, Any] = {
            "model": self._model_name,
            "config": config,
        }
        return contents, call_kwargs

    def format_assistant_message(self, response: LLMResponse) -> Any:
        parts = []
        if response.text:
            parts.append(types.Part(text=response.text))
        for tc in response.tool_calls:
            parts.append(
                types.Part(
                    function_call=types.FunctionCall(
                        name=tc.name,
                        args=tc.arguments,
                    ),
                ),
            )
        return types.Content(role="model", parts=parts)

    def format_tool_results(
        self,
        tool_calls: list[ToolCall],
        results: list[dict],
    ) -> list:
        function_response_parts = [
            types.Part(
                function_response=types.FunctionResponse(
                    name=tc.name,
                    response={"output": result["output"]},
                ),
            )
            for tc, result in zip(tool_calls, results)
        ]
        return [types.Content(role="user", parts=function_response_parts)]

    def format_user_message(self, text: str) -> Any:
        return types.Content(role="user", parts=[types.Part(text=text)])

    def remove_tools_from_kwargs(self, call_kwargs: dict) -> dict:
        old_config = call_kwargs.get("config")
        new_config_kwargs: dict[str, Any] = {}
        if old_config is not None:
            if hasattr(old_config, "system_instruction"):
                new_config_kwargs["system_instruction"] = old_config.system_instruction
            if hasattr(old_config, "max_output_tokens"):
                new_config_kwargs["max_output_tokens"] = old_config.max_output_tokens
        new_config = types.GenerateContentConfig(**new_config_kwargs)
        return {k: v for k, v in call_kwargs.items() if k not in ("config", "tools")} | {
            "config": new_config,
        }

    async def stream(
        self,
        messages: list,
        call_kwargs: dict,
    ) -> AsyncIterator[str]:
        client = genai.Client()
        model = call_kwargs.get("model", self._model_name)
        config = call_kwargs.get("config")
        async for chunk in await client.aio.models.generate_content_stream(
            model=model,
            contents=messages,
            config=config,
        ):
            if chunk.text:
                yield chunk.text


def _config_with_tools(config: Any, tools: list) -> Any:
    kwargs: dict[str, Any] = {}
    if hasattr(config, "system_instruction"):
        kwargs["system_instruction"] = config.system_instruction
    if hasattr(config, "max_output_tokens"):
        kwargs["max_output_tokens"] = config.max_output_tokens
    if hasattr(config, "response_mime_type") and config.response_mime_type:
        kwargs["response_mime_type"] = config.response_mime_type
    if hasattr(config, "response_schema") and config.response_schema:
        kwargs["response_schema"] = config.response_schema
    kwargs["tools"] = tools
    return types.GenerateContentConfig(**kwargs)


def _to_content(role: str, text: str) -> Any:
    gemini_role = "model" if role == "assistant" else role
    return types.Content(role=gemini_role, parts=[types.Part(text=text)])


def _to_llm_response(response: Any) -> LLMResponse:
    text = response.text or ""
    tool_calls: list[ToolCall] = []
    if response.function_calls:
        for fc in response.function_calls:
            tool_calls.append(
                ToolCall(
                    id=fc.name,
                    name=fc.name,
                    arguments=dict(fc.args),
                ),
            )
    return LLMResponse(text=text, tool_calls=tool_calls)


def _to_gemini_tools(schemas: list[dict[str, Any]]) -> list:
    declarations = [
        types.FunctionDeclaration(
            name=s["name"],
            description=s["description"],
            parameters_json_schema=s["parameters"],
        )
        for s in schemas
    ]
    return [types.Tool(function_declarations=declarations)]
