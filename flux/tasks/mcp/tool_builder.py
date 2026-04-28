from __future__ import annotations

import inspect
import re
from typing import Any

from flux.task import task
from flux.tasks.mcp.elicitation import ElicitationRequestOutput


def _handle_elicitation_error(error: Exception, server_name: str) -> ElicitationRequestOutput:
    data = error.data if hasattr(error, "data") else {}
    elicitations = data.get("elicitations", []) if isinstance(data, dict) else []
    if not elicitations:
        raise error

    elicitation = elicitations[0]
    return ElicitationRequestOutput(
        elicitation_id=elicitation.get("elicitationId", ""),
        url=elicitation.get("url", ""),
        message=elicitation.get("message", ""),
        server_name=server_name,
    )


def _extract_elicitation_action(resume_payload: Any) -> str:
    """Extract the elicitation action from a resume payload.

    The agent process resumes with either:
      - {"elicitation_response": {"elicitation_id": ..., "action": "accept"}}
      - An ElicitationResponse Pydantic model dumped to dict directly
      - {} or None when not resumed via the agent UI — treat as decline.

    Returns one of "accept", "decline", "cancel". Defaults to "decline" when
    the payload shape is unrecognized to fail closed.
    """
    if not resume_payload:
        return "decline"
    if isinstance(resume_payload, dict):
        nested = resume_payload.get("elicitation_response")
        if isinstance(nested, dict) and "action" in nested:
            return str(nested["action"])
        if "action" in resume_payload:
            return str(resume_payload["action"])
    return "decline"


JSON_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "object": dict,
    "array": list,
}

_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _sanitize_name(name: str) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    if sanitized and sanitized[0].isdigit():
        sanitized = f"_{sanitized}"
    return sanitized or "_unknown"


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
    param_names: list[str] = []
    param_name_to_original: dict[str, str] = {}

    for original_name, param_schema in properties.items():
        json_type = param_schema.get("type", "string")
        python_type = JSON_TYPE_MAP.get(json_type, str)

        safe_name = (
            original_name if _IDENTIFIER_RE.match(original_name) else _sanitize_name(original_name)
        )
        annotations[safe_name] = python_type
        param_names.append(safe_name)
        if safe_name != original_name:
            param_name_to_original[safe_name] = original_name

        if original_name in required:
            params.append(
                inspect.Parameter(
                    safe_name,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    annotation=python_type,
                ),
            )
        else:
            params.append(
                inspect.Parameter(
                    safe_name,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    default=None,
                    annotation=python_type,
                ),
            )

    sig = inspect.Signature(params)

    async def tool_func(*args: Any, **kwargs: Any) -> Any:
        from flux.tasks.mcp.errors import ToolExecutionError

        if len(args) > len(param_names):
            raise TypeError(
                f"{tool_name}() takes {len(param_names)} positional argument(s) but {len(args)} were given",
            )

        for i, val in enumerate(args):
            kwargs[param_names[i]] = val

        call_kwargs = {param_name_to_original.get(k, k): v for k, v in kwargs.items()}

        connection = await client._get_connection()
        try:
            result = await connection.call_tool(tool_name, call_kwargs)
        except ToolExecutionError:
            raise
        except (ConnectionError, OSError, TimeoutError) as e:
            await client._discard_connection()
            raise ToolExecutionError(tool_name, str(e), inner_exception=e)
        except Exception as e:
            if hasattr(e, "code") and e.code == -32042:
                from flux.tasks.pause import pause

                elicitation_output = _handle_elicitation_error(e, server_name=server_name)
                resume_payload = await pause(
                    f"elicitation_{tool_name}",
                    output=elicitation_output.model_dump(),
                )
                action = _extract_elicitation_action(resume_payload)
                if action == "accept":
                    try:
                        result = await connection.call_tool(tool_name, call_kwargs)
                    except ToolExecutionError:
                        raise
                    except (ConnectionError, OSError, TimeoutError) as retry_e:
                        await client._discard_connection()
                        raise ToolExecutionError(
                            tool_name,
                            str(retry_e),
                            inner_exception=retry_e,
                        )
                    except Exception as retry_e:
                        raise ToolExecutionError(
                            tool_name,
                            str(retry_e),
                            inner_exception=retry_e,
                        )
                else:
                    raise ToolExecutionError(
                        tool_name,
                        f"Elicitation {action} by user",
                    )
            else:
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

    safe_func_name = _sanitize_name(tool_name)
    tool_func.__name__ = safe_func_name
    tool_func.__qualname__ = safe_func_name
    tool_func.__doc__ = description
    setattr(tool_func, "__signature__", sig)  # noqa: B010
    tool_func.__annotations__ = annotations

    task_name = f"mcp_{_sanitize_name(server_name)}_{safe_func_name}"
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
