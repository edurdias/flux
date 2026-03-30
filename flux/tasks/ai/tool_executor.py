from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import typing
from typing import Any, Callable, Union, get_type_hints

logger = logging.getLogger("flux.agent")

# Patterns for tool calls embedded in text content.
# Models like Mistral, Qwen, and Hermes sometimes output tool calls as text
# instead of using the structured tool_calls field.
_TOOL_CALL_PATTERNS = [
    re.compile(r"\[TOOL_CALLS\]\s*(\[.*\])", re.DOTALL),
    re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL),
    re.compile(r"^\s*(\[\s*\{.*\}\s*\])\s*$", re.DOTALL),
]


def extract_tool_calls_from_content(
    content: str,
    tool_names: set[str],
) -> list[dict[str, Any]] | None:
    """Extract tool calls from text content when models output them as text.

    Some models (Mistral, Qwen, Hermes) sometimes embed tool calls in text
    content instead of the structured tool_calls field. This function detects
    known patterns and parses them.

    Only returns tool calls whose names match registered tools to avoid
    false positives from regular JSON in model output.

    Args:
        content: The text content from the model response.
        tool_names: Set of registered tool function names.

    Returns:
        List of tool call dicts (id, name, arguments) or None if not found.
    """
    if not content or not tool_names:
        return None

    for pattern in _TOOL_CALL_PATTERNS:
        matches = pattern.findall(content)
        if not matches:
            continue

        for match in matches:
            try:
                parsed = json.loads(match)
                if isinstance(parsed, dict):
                    parsed = [parsed]
                if not isinstance(parsed, list):
                    continue

                tool_calls: list[dict[str, Any]] = []
                for item in parsed:
                    name = item.get("name")
                    if name and name in tool_names:
                        tool_calls.append(
                            {
                                "id": f"call_{len(tool_calls)}",
                                "name": name,
                                "arguments": item.get(
                                    "arguments",
                                    item.get("parameters", {}),
                                ),
                            },
                        )

                if tool_calls:
                    logger.debug(
                        "Extracted %d tool call(s) from text content",
                        len(tool_calls),
                    )
                    return tool_calls
            except (json.JSONDecodeError, TypeError, AttributeError):
                continue

    return None


def strip_tool_calls_from_content(content: str) -> str:
    """Remove tool call patterns from text content.

    Used to clean up final responses when models embed tool calls
    in their text output on the last turn.
    """
    if not content:
        return content
    for pattern in _TOOL_CALL_PATTERNS:
        content = pattern.sub("", content)
    return content.strip()


def _serialize_result(result: Any) -> str:
    """Serialize a tool result to a string for the LLM.

    Dicts and lists are JSON-serialized to avoid Python repr output
    (single quotes, etc.) which is not valid JSON and confuses LLMs.
    """
    if isinstance(result, (dict, list)):
        import json

        return json.dumps(result)
    return str(result)


def _resolve_json_type(hint: Any) -> str:
    """Map a Python type hint to a JSON Schema type string."""
    if hint is str:
        return "string"
    if hint is int:
        return "integer"
    if hint is float:
        return "number"
    if hint is bool:
        return "boolean"
    if hint is list or typing.get_origin(hint) is list:
        return "array"
    if hint is dict or typing.get_origin(hint) is dict:
        return "object"

    origin = typing.get_origin(hint)
    if origin is Union:
        args = [a for a in typing.get_args(hint) if a is not type(None)]
        if args:
            return _resolve_json_type(args[0])

    return "string"


def build_tool_schemas(tools: list[Any]) -> list[dict[str, Any]]:
    """Build neutral tool schemas from Flux @task functions.

    Inspects each task's .func for its signature, docstring, and type hints.
    Returns a list of dicts with name, description, and parameters schema.
    """
    schemas = []
    for tool in tools:
        func = tool.func if hasattr(tool, "func") else tool
        sig = inspect.signature(func)
        hints = get_type_hints(func)

        parameters: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

        for param_name, param in sig.parameters.items():
            if param_name in ("self", "secrets", "metadata") or param_name.startswith("_"):
                continue
            if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                continue

            hint = hints.get(param_name)
            prop: dict[str, Any] = {"type": _resolve_json_type(hint)}
            parameters["properties"][param_name] = prop

            if param.default is inspect.Parameter.empty and param.kind != param.KEYWORD_ONLY:
                parameters["required"].append(param_name)

        schemas.append(
            {
                "name": func.__name__,
                "description": (func.__doc__ or "").strip(),
                "parameters": parameters,
            },
        )

    return schemas


async def execute_tools(
    tool_calls: list[dict[str, Any]],
    tools: list[Any],
    iteration: int = 0,
    max_concurrent: int | None = None,
) -> list[dict[str, Any]]:
    """Execute tool calls and return results.

    Each tool call is a dict with 'name' and 'arguments'.
    Tools are Flux @task functions — each invocation produces task events.

    Args:
        tool_calls: List of tool calls from the LLM.
        tools: List of Flux @task functions.
        iteration: The tool call iteration number within the agent call.
            Used to generate deterministic _call_id values for replay safety.
        max_concurrent: Maximum number of tools to run concurrently.
            When ``None`` (the default) all tools run in parallel with no limit.
            Set to ``1`` for fully sequential execution.
    """
    tool_map: dict[str, Callable] = {}
    for tool in tools:
        func = tool.func if hasattr(tool, "func") else tool
        tool_map[func.__name__] = tool

    async def _run_one(call: dict[str, Any]) -> dict[str, Any]:
        name = call["name"]
        args = call.get("arguments", {})
        if isinstance(args, str):
            import json

            args = json.loads(args)

        tool_fn = tool_map.get(name)
        if not tool_fn:
            logger.warning("Agent requested unknown tool: %s", name)
            return {"tool_call_id": call.get("id", name), "output": f"Error: Unknown tool '{name}'"}

        # Filter args to only include parameters the function accepts.
        # LLMs sometimes hallucinate parameter names not in the schema.
        func = tool_fn.func if hasattr(tool_fn, "func") else tool_fn
        sig = inspect.signature(func)
        has_var_keyword = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )
        if not has_var_keyword:
            accepted = set(sig.parameters.keys()) - {"self", "secrets", "metadata"}
            unknown = set(args.keys()) - accepted
            if unknown:
                logger.debug("Tool '%s': dropping unknown args %s", name, unknown)
                args = {k: v for k, v in args.items() if k in accepted}

        try:
            call_id = call.get("id", name)
            effective_tool = tool_fn
            if iteration > 0 and hasattr(tool_fn, "with_options"):
                effective_tool = tool_fn.with_options(name=f"{name}_{iteration}")
            result = await effective_tool(**args)
            return {"tool_call_id": call_id, "output": _serialize_result(result)}
        except Exception as e:
            logger.warning("Tool '%s' failed: %s (args=%s)", name, e, args)
            return {"tool_call_id": call.get("id", name), "output": f"Error: {e!s}"}

    if max_concurrent is not None:
        if max_concurrent < 1:
            raise ValueError(f"max_concurrent must be >= 1, got {max_concurrent}")
        sem = asyncio.Semaphore(max_concurrent)

        async def _limited(call: dict[str, Any]) -> dict[str, Any]:
            async with sem:
                return await _run_one(call)

        return list(await asyncio.gather(*[_limited(c) for c in tool_calls]))

    return list(await asyncio.gather(*[_run_one(c) for c in tool_calls]))
