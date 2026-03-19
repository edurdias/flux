from __future__ import annotations

from flux.errors import ExecutionError
from flux.tasks.mcp.errors import ToolExecutionError


def test_tool_execution_error_basic():
    err = ToolExecutionError("get_weather", "Server timeout")
    assert isinstance(err, ExecutionError)
    assert err.tool_name == "get_weather"
    assert "get_weather" in str(err)
    assert "Server timeout" in str(err)


def test_tool_execution_error_with_inner():
    cause = RuntimeError("connection lost")
    err = ToolExecutionError("search", "failed", inner_exception=cause)
    assert err.inner_exception is cause
    assert err.tool_name == "search"
