from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable, get_type_hints


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

            prop: dict[str, Any] = {}
            hint = hints.get(param_name)
            if hint is str:
                prop["type"] = "string"
            elif hint is int:
                prop["type"] = "integer"
            elif hint is float:
                prop["type"] = "number"
            elif hint is bool:
                prop["type"] = "boolean"
            else:
                prop["type"] = "string"

            if param_name in hints:
                prop["description"] = param_name

            parameters["properties"][param_name] = prop

            if param.default is inspect.Parameter.empty and param.kind != param.KEYWORD_ONLY:
                parameters["required"].append(param_name)

        schemas.append({
            "name": func.__name__,
            "description": (func.__doc__ or "").strip(),
            "parameters": parameters,
        })

    return schemas


async def execute_tools(
    tool_calls: list[dict[str, Any]],
    tools: list[Any],
) -> list[dict[str, Any]]:
    """Execute tool calls and return results.

    Each tool call is a dict with 'name' and 'arguments'.
    Tools are Flux @task functions — each invocation produces task events.
    Multiple calls execute concurrently via asyncio.gather.
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
            return {"tool_call_id": call.get("id", name), "output": f"Error: Unknown tool '{name}'"}

        try:
            result = await tool_fn(**args)
            return {"tool_call_id": call.get("id", name), "output": str(result)}
        except Exception as e:
            return {"tool_call_id": call.get("id", name), "output": f"Error: {e!s}"}

    results = await asyncio.gather(*[_run_one(call) for call in tool_calls])
    return list(results)
