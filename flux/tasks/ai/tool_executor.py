from __future__ import annotations

import inspect
import logging
import typing
from typing import Any, Callable, Union, get_type_hints

logger = logging.getLogger("flux.agent")


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
) -> list[dict[str, Any]]:
    """Execute tool calls and return results.

    Each tool call is a dict with 'name' and 'arguments'.
    Tools are Flux @task functions — each invocation produces task events.

    Args:
        tool_calls: List of tool calls from the LLM.
        tools: List of Flux @task functions.
        iteration: The tool call iteration number within the agent call.
            Used to generate deterministic _call_id values for replay safety.
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

        try:
            import json as _json

            call_id = call.get("id", name)
            effective_tool = tool_fn
            if iteration > 0 and hasattr(tool_fn, "with_options"):
                effective_tool = tool_fn.with_options(name=f"{name}_{iteration}")
            result = await effective_tool(**args)
            if isinstance(result, (dict, list)):
                output = _json.dumps(result)
            else:
                output = str(result)
            return {"tool_call_id": call_id, "output": output}
        except Exception as e:
            logger.warning("Tool '%s' failed: %s", name, e)
            return {"tool_call_id": call.get("id", name), "output": f"Error: {e!s}"}

    return [await _run_one(call) for call in tool_calls]
