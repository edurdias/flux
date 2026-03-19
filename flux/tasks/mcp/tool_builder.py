from __future__ import annotations

import inspect
from typing import Any

from flux.task import task


JSON_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "object": dict,
    "array": list,
}


def build_tool_task(
    schema: dict[str, Any],
    client: Any,
    server_name: str,
    **task_options: Any,
) -> task:
    tool_name = schema["name"]
    description = schema.get("description", "")
    input_schema = schema.get("inputSchema", {})
    properties = input_schema.get("properties", {})
    required = set(input_schema.get("required", []))

    params = []
    annotations = {}

    for param_name, param_schema in properties.items():
        json_type = param_schema.get("type", "string")
        python_type = JSON_TYPE_MAP.get(json_type, str)
        annotations[param_name] = python_type

        if param_name in required:
            params.append(
                inspect.Parameter(
                    param_name,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    annotation=python_type,
                ),
            )
        else:
            params.append(
                inspect.Parameter(
                    param_name,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    default=None,
                    annotation=python_type,
                ),
            )

    sig = inspect.Signature(params)

    param_names = list(properties.keys())

    async def tool_func(*args: Any, **kwargs: Any) -> Any:
        from flux.tasks.mcp.errors import ToolExecutionError

        for i, val in enumerate(args):
            if i < len(param_names):
                kwargs[param_names[i]] = val

        connection = await client._get_connection()
        try:
            result = await connection.call_tool(tool_name, kwargs)
        except ToolExecutionError:
            raise
        except Exception as e:
            is_connection_error = "connect" in str(e).lower() or "timeout" in str(e).lower()
            if is_connection_error:
                await client._discard_connection()
            raise ToolExecutionError(tool_name, str(e), inner_exception=e)
        finally:
            if client._connection == "per-call":
                await client._close_connection(connection)

        if isinstance(result, list):
            content = result
        else:
            if result.is_error:
                texts = [c.text for c in result.content if hasattr(c, "text")]
                raise ToolExecutionError(tool_name, "; ".join(texts) or "Unknown error")
            content = result.content

        if content:
            first = content[0]
            return first.text if hasattr(first, "text") else str(first)
        return None

    tool_func.__name__ = tool_name
    tool_func.__qualname__ = tool_name
    tool_func.__doc__ = description
    setattr(tool_func, "__signature__", sig)  # noqa: B010
    tool_func.__annotations__ = annotations

    task_name = f"mcp_{server_name}_{tool_name}"
    return task(
        func=tool_func,
        name=task_name,
        **task_options,
    )


def build_tools(
    schemas: list[dict[str, Any]],
    client: Any,
    server_name: str,
    **task_options: Any,
) -> dict[str, task]:
    return {
        schema["name"]: build_tool_task(schema, client, server_name, **task_options)
        for schema in schemas
    }
