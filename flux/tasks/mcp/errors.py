from __future__ import annotations

from flux.errors import ExecutionError


class ToolExecutionError(ExecutionError):
    def __init__(
        self,
        tool_name: str,
        message: str,
        inner_exception: Exception | None = None,
    ):
        self.tool_name = tool_name
        super().__init__(
            inner_exception,
            message=f"MCP tool '{tool_name}' failed: {message}",
        )
