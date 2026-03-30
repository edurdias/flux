from __future__ import annotations

import asyncio

import pytest

from flux.domain.execution_context import ExecutionContext
from flux.tasks.ai.tools.system_tools import SystemToolsConfig


@pytest.fixture
def config(tmp_path):
    return SystemToolsConfig(
        workspace=tmp_path,
        timeout=30,
        blocklist=[r"\brm\s+-rf\s+/", r"\bshutdown\b"],
        max_output_chars=100_000,
    )


@pytest.fixture
def shell_tool(config):
    from flux.tasks.ai.tools.shell import build_shell_tools

    tools = build_shell_tools(config)
    assert len(tools) == 1
    return tools[0]


def _run(coro):
    async def _wrapper():
        ctx = ExecutionContext(workflow_id="test", workflow_name="test")
        token = ExecutionContext.set(ctx)
        try:
            return await coro
        finally:
            ExecutionContext.reset(token)

    return asyncio.run(_wrapper())


def test_build_shell_tools_returns_one_tool(config):
    from flux.tasks.ai.tools.shell import build_shell_tools

    tools = build_shell_tools(config)
    assert len(tools) == 1
    assert tools[0].func.__name__ == "shell"


def test_shell_echo(shell_tool):
    result = _run(shell_tool(command="echo hello"))
    assert result["status"] == "ok"
    assert result["exit_code"] == 0
    assert "hello" in result["stdout"]


def test_shell_stderr(shell_tool):
    result = _run(shell_tool(command="echo err >&2"))
    assert result["status"] == "ok"
    assert "err" in result["stderr"]


def test_shell_nonzero_exit_is_ok(shell_tool):
    result = _run(shell_tool(command="exit 42"))
    assert result["status"] == "ok"
    assert result["exit_code"] == 42


def test_shell_cwd_is_workspace(shell_tool, tmp_path):
    result = _run(shell_tool(command="pwd"))
    assert result["status"] == "ok"
    assert str(tmp_path) in result["stdout"]


def test_shell_blocklist_blocks(shell_tool):
    result = _run(shell_tool(command="rm -rf /"))
    assert result["status"] == "error"
    assert "blocked" in result["error"].lower()


def test_shell_blocklist_blocks_shutdown(shell_tool):
    result = _run(shell_tool(command="shutdown now"))
    assert result["status"] == "error"


def test_shell_allowed_commands_pass(shell_tool):
    result = _run(shell_tool(command="ls -la"))
    assert result["status"] == "ok"


def test_shell_truncates_stdout(tmp_path):
    config = SystemToolsConfig(
        workspace=tmp_path,
        timeout=30,
        blocklist=[],
        max_output_chars=50,
    )
    from flux.tasks.ai.tools.shell import build_shell_tools

    tool = build_shell_tools(config)[0]
    result = _run(tool(command="python3 -c \"print('x' * 200)\""))
    assert result["status"] == "ok"
    assert result["truncated"] is True
    assert result["total_chars"] > 50
    assert len(result["stdout"]) == 50
